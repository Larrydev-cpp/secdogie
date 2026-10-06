//! Deterministic action preview: what acting on a known state would be, in the
//! `secdogie/action-authorization/v1` field set.
//!
//! This runs only after Gate 1 has aligned an intent with a concrete state. It
//! does no inference: the state must already be in the graph, a `submit` needs
//! a form reference that the markup actually named, and every field of the
//! result is a pure function of the graph. Nothing here executes anything --
//! the preview is what Gate 2 hashes and a human signs.
//!
//! Risk is assigned conservatively:
//!
//! * `navigate`, or a `get` form: `low` (reads, `high_risk = false`);
//! * a `post` form: `high` -- it changes server state, so it needs Gate 2;
//! * a `post` form whose route or field names say delete / remove / close /
//!   cancel / ...: `irreversible`.

use crate::canon::{Value, obj, str_arr};
use crate::dag::DagStore;
use crate::delta::{Method, Reference, Via};

const DESTRUCTIVE_WORDS: [&str; 16] = [
    "delete",
    "remove",
    "destroy",
    "drop",
    "erase",
    "purge",
    "wipe",
    "close",
    "cancel",
    "deactivate",
    "terminate",
    "unsubscribe",
    "revoke",
    "删除",
    "注销",
    "销毁",
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Verb {
    Navigate,
    Submit,
}

impl Verb {
    pub fn parse(s: &str) -> Option<Verb> {
        match s {
            "navigate" => Some(Verb::Navigate),
            "submit" => Some(Verb::Submit),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Risk {
    Low,
    High,
    Irreversible,
}

impl Risk {
    pub fn as_str(self) -> &'static str {
        match self {
            Risk::Low => "low",
            Risk::High => "high",
            Risk::Irreversible => "irreversible",
        }
    }
}

/// The six fields `authz.action_hash` commits to.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TargetAction {
    pub kind: String,
    pub target_id: String,
    pub target_role: String,
    pub target_name: String,
    pub text: String,
    pub high_risk: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Preview {
    pub target_action: TargetAction,
    pub risk: Risk,
    pub mutating: bool,
    pub method: Method,
    pub fields: Vec<String>,
    pub origin: String,
    pub route: String,
}

impl Preview {
    pub fn to_value(&self) -> Value {
        let a = &self.target_action;
        obj([
            (
                "target_action",
                obj([
                    ("kind", Value::str(&a.kind)),
                    ("target_id", Value::str(&a.target_id)),
                    ("target_role", Value::str(&a.target_role)),
                    ("target_name", Value::str(&a.target_name)),
                    ("text", Value::str(&a.text)),
                    ("high_risk", Value::Bool(a.high_risk)),
                ]),
            ),
            ("risk", Value::str(self.risk.as_str())),
            ("mutating", Value::Bool(self.mutating)),
            ("method", Value::str(self.method.as_str())),
            ("fields", str_arr(self.fields.iter().cloned())),
            ("origin", Value::str(&self.origin)),
            ("route", Value::str(&self.route)),
        ])
    }
}

fn mentions_destruction(s: &str) -> bool {
    let lower = s.to_lowercase();
    DESTRUCTIVE_WORDS.iter().any(|w| lower.contains(w))
}

pub fn plan(store: &DagStore, state_key: &str, verb: Verb) -> Result<Preview, String> {
    let view = store.view();
    let Some(state) = view.states.get(state_key) else {
        return Err("state is not in the graph: Gate 2 never signs an invented target".into());
    };
    let target_name = format!("{}{}", state.origin, state.route);
    match verb {
        Verb::Navigate => Ok(Preview {
            target_action: TargetAction {
                kind: "navigate".into(),
                target_id: state_key.into(),
                target_role: "link".into(),
                target_name,
                text: String::new(),
                high_risk: false,
            },
            risk: Risk::Low,
            mutating: false,
            method: Method::Get,
            fields: vec![],
            origin: state.origin.clone(),
            route: state.route.clone(),
        }),
        Verb::Submit => {
            // Deterministic choice among the forms that reach this state: the
            // greatest (post sorts after get), so a mutating form is never hidden
            // behind a harmless one.
            let form: Option<&Reference> = view
                .references
                .iter()
                .filter(|r| r.via == Via::Form && r.to == state_key)
                .max();
            let Some(form) = form else {
                return Err("no form in the graph submits to this state".into());
            };
            let mutating = form.method == Method::Post;
            let destructive = mutating
                && (mentions_destruction(&state.route)
                    || form.fields.iter().any(|f| mentions_destruction(f)));
            let risk = if destructive {
                Risk::Irreversible
            } else if mutating {
                Risk::High
            } else {
                Risk::Low
            };
            Ok(Preview {
                target_action: TargetAction {
                    kind: "submit".into(),
                    target_id: state_key.into(),
                    target_role: "form".into(),
                    target_name,
                    text: form.fields.join(","),
                    high_risk: risk != Risk::Low,
                },
                risk,
                mutating,
                method: form.method,
                fields: form.fields.clone(),
                origin: state.origin.clone(),
                route: state.route.clone(),
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dag::tests::{signed, store};
    use crate::delta::{Op, state_key as key};
    use crate::envelope::testkit::Key;

    const O: &str = "https://docs.example.com";

    fn st(route: &str) -> Op {
        Op::AddState {
            origin: O.into(),
            route: route.into(),
            query_keys: vec![],
        }
    }

    fn form(from: &str, to: &str, method: Method, fields: &[&str]) -> Op {
        Op::AddReference(Reference {
            from: key(O, from, &[]),
            to: key(O, to, &[]),
            via: Via::Form,
            method,
            fields: fields.iter().map(|s| s.to_string()).collect(),
        })
    }

    #[test]
    fn previews_are_pure_functions_of_the_graph() {
        let k = Key::from_label("agent");
        let mut s = store(&k);
        let (_, w) = signed(
            &k,
            &[],
            1,
            vec![
                st("/settings"),
                st("/account/delete"),
                st("/search"),
                st("/profile"),
                form(
                    "/settings",
                    "/account/delete",
                    Method::Post,
                    &["confirm", "reason"],
                ),
                form("/settings", "/search", Method::Get, &["q"]),
                form("/settings", "/profile", Method::Post, &["bio"]),
            ],
        );
        s.insert_wire(&w).unwrap();

        let del = plan(&s, &key(O, "/account/delete", &[]), Verb::Submit).unwrap();
        assert_eq!(del.risk, Risk::Irreversible);
        assert!(del.target_action.high_risk);
        assert_eq!(del.target_action.text, "confirm,reason");
        assert_eq!(
            del.target_action.target_name,
            "https://docs.example.com/account/delete"
        );

        assert_eq!(
            plan(&s, &key(O, "/profile", &[]), Verb::Submit)
                .unwrap()
                .risk,
            Risk::High
        );
        let search = plan(&s, &key(O, "/search", &[]), Verb::Submit).unwrap();
        assert_eq!(
            (search.risk, search.target_action.high_risk),
            (Risk::Low, false)
        );
        assert_eq!(
            plan(&s, &key(O, "/profile", &[]), Verb::Navigate)
                .unwrap()
                .risk,
            Risk::Low
        );

        assert!(plan(&s, &key(O, "/never-seen", &[]), Verb::Navigate).is_err());
        assert!(plan(&s, &key(O, "/settings", &[]), Verb::Submit).is_err());
    }
}
