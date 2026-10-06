//! The content-addressed DAG of signed deltas and the topology it materializes.
//!
//! `insert` is the only way in, and it checks, in this order:
//!
//! 1. the envelope's Ed25519 signature (`envelope::verify`);
//! 2. that the signer is the delta's `author` -- a key cannot write as someone else;
//! 3. that the author is trusted (zero trust: the set is never empty);
//! 4. the strict v1 schema (`delta::parse_payload`);
//! 5. once every parent is present: `lamport == 1 + max(parent lamport)`
//!    (`1` for a root), so the clock is a function of the DAG, not a claim.
//!
//! A delta whose parents have not arrived yet is held as an *orphan* (already
//! authenticated, so a stranger cannot fill the buffer) and promoted as soon
//! as they do. The orphan buffer is bounded; the store itself is bounded and
//! refuses new deltas when full rather than evicting -- there is no deletion
//! in v1.
//!
//! The topology view is the union of every accepted op. Union is commutative
//! and idempotent, so two stores holding the same set of deltas hold the same
//! view whatever order they arrived in.

use std::collections::{BTreeMap, BTreeSet, VecDeque};

use crate::canon::{self, Value, obj, str_arr};
use crate::delta::{self, GraphDelta, Op, Reference};
use crate::envelope::{self, Trust};

#[derive(Clone, Debug)]
pub struct Config {
    pub trusted_authors: Vec<String>,
    pub max_nodes: usize,
    pub max_orphans: usize,
    pub max_envelope_bytes: usize,
}

impl Config {
    pub fn new(trusted_authors: Vec<String>) -> Config {
        Config {
            trusted_authors,
            max_nodes: 100_000,
            max_orphans: 1024,
            max_envelope_bytes: 64 * 1024,
        }
    }
}

#[derive(Clone, Debug)]
pub struct Node {
    pub cid: String,
    pub delta: GraphDelta,
    /// The envelope's canonical text, as it travels.
    pub wire: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Outcome {
    /// Accepted. `promoted` are orphans that became insertable because of it;
    /// `dropped` are orphans that turned out invalid once their parents arrived.
    Inserted {
        cid: String,
        promoted: Vec<String>,
        dropped: Vec<(String, String)>,
    },
    Duplicate {
        cid: String,
    },
    /// Authenticated and well-formed, waiting for `missing` parents.
    Pending {
        cid: String,
        missing: Vec<String>,
    },
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Rejected(pub String);

#[derive(Clone, Debug, Default, PartialEq, Eq, PartialOrd, Ord)]
pub struct Observation {
    pub content_hash: String,
    pub byte_len: u64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StateInfo {
    pub origin: String,
    pub route: String,
    pub query_keys: Vec<String>,
}

/// Grow-only sets: states by key, references, observations by state key.
#[derive(Clone, Debug, Default)]
pub struct TopologyView {
    pub states: BTreeMap<String, StateInfo>,
    pub references: BTreeSet<Reference>,
    pub observations: BTreeMap<String, BTreeSet<Observation>>,
}

impl TopologyView {
    fn apply(&mut self, ops: &[Op]) {
        for op in ops {
            match op {
                Op::AddState {
                    origin,
                    route,
                    query_keys,
                } => {
                    let key = delta::state_key(origin, route, query_keys);
                    self.states.entry(key).or_insert_with(|| StateInfo {
                        origin: origin.clone(),
                        route: route.clone(),
                        query_keys: query_keys.clone(),
                    });
                }
                Op::AddReference(r) => {
                    self.references.insert(r.clone());
                }
                Op::AddObservation {
                    state,
                    content_hash,
                    byte_len,
                } => {
                    self.observations
                        .entry(state.clone())
                        .or_default()
                        .insert(Observation {
                            content_hash: content_hash.clone(),
                            byte_len: *byte_len,
                        });
                }
            }
        }
    }

    /// A reference to a state no delta has declared (yet) is kept and marked
    /// dangling -- the view never invents the missing state.
    pub fn is_dangling(&self, r: &Reference) -> bool {
        !self.states.contains_key(&r.from) || !self.states.contains_key(&r.to)
    }

    pub fn to_value(&self) -> Value {
        let states = self
            .states
            .iter()
            .map(|(k, s)| {
                let obs = self
                    .observations
                    .get(k)
                    .map(|o| o.iter().collect::<Vec<_>>())
                    .unwrap_or_default();
                obj([
                    ("key", Value::str(k)),
                    ("origin", Value::str(&s.origin)),
                    ("route", Value::str(&s.route)),
                    ("query_keys", str_arr(s.query_keys.iter().cloned())),
                    (
                        "observations",
                        Value::Arr(
                            obs.iter()
                                .map(|o| {
                                    obj([
                                        ("content_hash", Value::str(&o.content_hash)),
                                        ("byte_len", Value::Int(o.byte_len.to_string())),
                                    ])
                                })
                                .collect(),
                        ),
                    ),
                ])
            })
            .collect();
        let refs = self
            .references
            .iter()
            .map(|r| {
                obj([
                    ("from", Value::str(&r.from)),
                    ("to", Value::str(&r.to)),
                    ("via", Value::str(r.via.as_str())),
                    ("method", Value::str(r.method.as_str())),
                    ("fields", str_arr(r.fields.iter().cloned())),
                    ("dangling", Value::Bool(self.is_dangling(r))),
                ])
            })
            .collect();
        obj([
            ("states", Value::Arr(states)),
            ("references", Value::Arr(refs)),
        ])
    }
}

pub struct DagStore {
    trust: Trust,
    cfg: Config,
    nodes: BTreeMap<String, Node>,
    heads: BTreeSet<String>,
    orphans: BTreeMap<String, Node>,
    orphan_order: VecDeque<String>,
    waiting_on: BTreeMap<String, BTreeSet<String>>,
    view: TopologyView,
}

impl DagStore {
    pub fn new(cfg: Config) -> Result<DagStore, Rejected> {
        let trust =
            Trust::new(cfg.trusted_authors.iter().cloned()).map_err(|e| Rejected(e.into()))?;
        Ok(DagStore {
            trust,
            cfg,
            nodes: BTreeMap::new(),
            heads: BTreeSet::new(),
            orphans: BTreeMap::new(),
            orphan_order: VecDeque::new(),
            waiting_on: BTreeMap::new(),
            view: TopologyView::default(),
        })
    }

    pub fn len(&self) -> usize {
        self.nodes.len()
    }

    pub fn is_empty(&self) -> bool {
        self.nodes.is_empty()
    }

    pub fn heads(&self) -> Vec<String> {
        self.heads.iter().cloned().collect()
    }

    pub fn contains(&self, cid: &str) -> bool {
        self.nodes.contains_key(cid)
    }

    /// Known as a node or held as an orphan: no need to ask a peer for it.
    pub fn knows(&self, cid: &str) -> bool {
        self.nodes.contains_key(cid) || self.orphans.contains_key(cid)
    }

    pub fn get(&self, cid: &str) -> Option<&Node> {
        self.nodes.get(cid)
    }

    pub fn view(&self) -> &TopologyView {
        &self.view
    }

    pub fn orphan_count(&self) -> usize {
        self.orphans.len()
    }

    /// Parents some held orphan is still waiting for.
    pub fn missing_parents(&self) -> Vec<String> {
        self.waiting_on
            .keys()
            .filter(|c| !self.knows(c))
            .cloned()
            .collect()
    }

    /// Inserts one envelope given as JSON text.
    pub fn insert_wire(&mut self, wire: &str) -> Result<Outcome, Rejected> {
        if wire.len() > self.cfg.max_envelope_bytes {
            return Err(Rejected("envelope too large".into()));
        }
        let v = canon::parse(wire).map_err(|e| Rejected(format!("not canonical JSON: {e}")))?;
        self.insert_value(&v)
    }

    pub fn insert_value(&mut self, v: &Value) -> Result<Outcome, Rejected> {
        let Some(m) = v.as_obj() else {
            return Err(Rejected("envelope must be an object".into()));
        };
        let verified = envelope::verify(m).map_err(|e| Rejected(e.reason().into()))?;
        let author = verified
            .payload
            .as_obj()
            .and_then(|p| p.get("author"))
            .and_then(Value::as_str);
        if author != Some(verified.signer.as_str()) {
            return Err(Rejected("signer is not the author".into()));
        }
        if !self.trust.contains(&verified.signer) {
            return Err(Rejected("untrusted author".into()));
        }
        let cid = delta::sha256_hex(&verified.payload_bytes);
        if self.nodes.contains_key(&cid) {
            return Ok(Outcome::Duplicate { cid });
        }
        if let Some(o) = self.orphans.get(&cid) {
            let missing = o
                .delta
                .parents
                .iter()
                .filter(|p| !self.nodes.contains_key(*p))
                .cloned()
                .collect();
            return Ok(Outcome::Pending { cid, missing });
        }
        let d = delta::parse_payload(&verified.payload).map_err(|e| Rejected(e.0))?;
        let node = Node {
            cid: cid.clone(),
            delta: d,
            wire: canon::to_string(v),
        };
        let missing: Vec<String> = node
            .delta
            .parents
            .iter()
            .filter(|p| !self.nodes.contains_key(*p))
            .cloned()
            .collect();
        if !missing.is_empty() {
            self.hold(node);
            return Ok(Outcome::Pending { cid, missing });
        }
        self.accept(node)?;
        let (promoted, dropped) = self.promote(&cid);
        Ok(Outcome::Inserted {
            cid,
            promoted,
            dropped,
        })
    }

    fn expected_lamport(&self, d: &GraphDelta) -> u64 {
        1 + d
            .parents
            .iter()
            .filter_map(|p| self.nodes.get(p))
            .map(|n| n.delta.lamport)
            .max()
            .unwrap_or(0)
    }

    fn accept(&mut self, node: Node) -> Result<(), Rejected> {
        let want = self.expected_lamport(&node.delta);
        if node.delta.lamport != want {
            return Err(Rejected(format!(
                "lamport must be 1 + max(parent lamport) = {want}, got {}",
                node.delta.lamport
            )));
        }
        if self.nodes.len() >= self.cfg.max_nodes {
            return Err(Rejected(
                "store full: v1 never evicts, so it refuses new deltas".into(),
            ));
        }
        for p in &node.delta.parents {
            self.heads.remove(p);
        }
        self.heads.insert(node.cid.clone());
        self.view.apply(&node.delta.ops);
        self.nodes.insert(node.cid.clone(), node);
        Ok(())
    }

    fn hold(&mut self, node: Node) {
        if self.orphans.len() >= self.cfg.max_orphans {
            if let Some(old) = self.orphan_order.pop_front() {
                self.forget_orphan(&old);
            }
        }
        for p in &node.delta.parents {
            if !self.nodes.contains_key(p) {
                self.waiting_on
                    .entry(p.clone())
                    .or_default()
                    .insert(node.cid.clone());
            }
        }
        self.orphan_order.push_back(node.cid.clone());
        self.orphans.insert(node.cid.clone(), node);
    }

    fn forget_orphan(&mut self, cid: &str) {
        if let Some(o) = self.orphans.remove(cid) {
            for p in &o.delta.parents {
                if let Some(w) = self.waiting_on.get_mut(p) {
                    w.remove(cid);
                    if w.is_empty() {
                        self.waiting_on.remove(p);
                    }
                }
            }
        }
        self.orphan_order.retain(|c| c != cid);
    }

    /// Inserts every orphan that `cid`'s arrival completed, transitively.
    fn promote(&mut self, cid: &str) -> (Vec<String>, Vec<(String, String)>) {
        let mut promoted = Vec::new();
        let mut dropped = Vec::new();
        let mut work = vec![cid.to_string()];
        while let Some(arrived) = work.pop() {
            let Some(waiters) = self.waiting_on.remove(&arrived) else {
                continue;
            };
            for w in waiters {
                let ready = self
                    .orphans
                    .get(&w)
                    .is_some_and(|o| o.delta.parents.iter().all(|p| self.nodes.contains_key(p)));
                if !ready {
                    continue;
                }
                let Some(node) = self.orphans.get(&w).cloned() else {
                    continue;
                };
                self.forget_orphan(&w);
                match self.accept(node) {
                    Ok(()) => {
                        promoted.push(w.clone());
                        work.push(w);
                    }
                    Err(Rejected(reason)) => dropped.push((w, reason)),
                }
            }
        }
        (promoted, dropped)
    }

    /// Every known ancestor of `cids`, inclusive.
    pub fn ancestors(&self, cids: &[String]) -> BTreeSet<String> {
        let mut seen = BTreeSet::new();
        let mut stack: Vec<&str> = cids
            .iter()
            .map(String::as_str)
            .filter(|c| self.nodes.contains_key(*c))
            .collect();
        while let Some(c) = stack.pop() {
            if !seen.insert(c.to_string()) {
                continue;
            }
            if let Some(n) = self.nodes.get(c) {
                stack.extend(
                    n.delta
                        .parents
                        .iter()
                        .map(String::as_str)
                        .filter(|p| self.nodes.contains_key(*p)),
                );
            }
        }
        seen
    }

    /// Nodes in a topological order: by (lamport, cid). Parents always have a
    /// smaller lamport, so they always come first.
    pub fn topo<'a>(&'a self, cids: impl IntoIterator<Item = &'a String>) -> Vec<&'a Node> {
        let mut v: Vec<&Node> = cids.into_iter().filter_map(|c| self.nodes.get(c)).collect();
        v.sort_by(|a, b| (a.delta.lamport, &a.cid).cmp(&(b.delta.lamport, &b.cid)));
        v
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::delta::{DELTA_TYPE, payload_value};
    use crate::envelope::testkit::Key;

    pub fn add_state(route: &str) -> Op {
        Op::AddState {
            origin: "https://docs.example.com".into(),
            route: route.into(),
            query_keys: vec![],
        }
    }

    pub fn signed(k: &Key, parents: &[&str], lamport: u64, ops: Vec<Op>) -> (String, String) {
        let mut parents: Vec<String> = parents.iter().map(|s| s.to_string()).collect();
        parents.sort();
        let d = GraphDelta {
            author: k.did(),
            parents,
            lamport,
            ops,
        };
        let payload = payload_value(&d);
        let cid = delta::cid_of(&payload);
        (cid, canon::to_string(&k.sign(&payload)))
    }

    pub fn store(k: &Key) -> DagStore {
        DagStore::new(Config::new(vec![k.did()])).unwrap()
    }

    #[test]
    fn insert_advances_heads_and_materializes_the_view() {
        let k = Key::from_label("agent");
        let mut s = store(&k);
        let (a, wa) = signed(&k, &[], 1, vec![add_state("/a")]);
        assert_eq!(
            s.insert_wire(&wa).unwrap(),
            Outcome::Inserted {
                cid: a.clone(),
                promoted: vec![],
                dropped: vec![]
            }
        );
        assert_eq!(s.heads(), vec![a.clone()]);
        let (b, wb) = signed(&k, &[&a], 2, vec![add_state("/b")]);
        s.insert_wire(&wb).unwrap();
        assert_eq!(s.heads(), vec![b.clone()]);
        assert_eq!(s.insert_wire(&wb).unwrap(), Outcome::Duplicate { cid: b });
        assert_eq!(s.view().states.len(), 2);
    }

    #[test]
    fn concurrent_branches_are_both_heads_and_merge() {
        let k = Key::from_label("agent");
        let mut s = store(&k);
        let (a, wa) = signed(&k, &[], 1, vec![add_state("/a")]);
        let (b, wb) = signed(&k, &[&a], 2, vec![add_state("/b")]);
        let (c, wc) = signed(&k, &[&a], 2, vec![add_state("/c")]);
        for w in [&wa, &wb, &wc] {
            s.insert_wire(w).unwrap();
        }
        let mut heads = vec![b.clone(), c.clone()];
        heads.sort();
        assert_eq!(s.heads(), heads);
        let (m, wm) = signed(&k, &[&b, &c], 3, vec![add_state("/m")]);
        s.insert_wire(&wm).unwrap();
        assert_eq!(s.heads(), vec![m]);
    }

    #[test]
    fn an_orphan_waits_for_its_parent_then_is_promoted() {
        let k = Key::from_label("agent");
        let mut s = store(&k);
        let (a, wa) = signed(&k, &[], 1, vec![add_state("/a")]);
        let (b, wb) = signed(&k, &[&a], 2, vec![add_state("/b")]);
        let (c, wc) = signed(&k, &[&b], 3, vec![add_state("/c")]);
        assert_eq!(
            s.insert_wire(&wc).unwrap(),
            Outcome::Pending {
                cid: c.clone(),
                missing: vec![b.clone()]
            }
        );
        assert_eq!(
            s.insert_wire(&wb).unwrap(),
            Outcome::Pending {
                cid: b.clone(),
                missing: vec![a.clone()]
            }
        );
        assert_eq!(s.missing_parents(), vec![a.clone()]);
        assert_eq!(
            s.insert_wire(&wa).unwrap(),
            Outcome::Inserted {
                cid: a,
                promoted: vec![b, c.clone()],
                dropped: vec![]
            }
        );
        assert_eq!(s.heads(), vec![c]);
        assert_eq!(s.orphan_count(), 0);
    }

    #[test]
    fn rejects_forgery_tombstones_bad_clocks_and_strangers() {
        let k = Key::from_label("agent");
        let stranger = Key::from_label("stranger");
        let mut s = store(&k);
        let (a, wa) = signed(&k, &[], 1, vec![add_state("/a")]);
        s.insert_wire(&wa).unwrap();

        let (_, skew) = signed(&k, &[&a], 7, vec![add_state("/b")]);
        assert!(s.insert_wire(&skew).unwrap_err().0.contains("lamport"));

        let (_, foreign) = signed(&stranger, &[], 1, vec![add_state("/x")]);
        assert_eq!(s.insert_wire(&foreign).unwrap_err().0, "untrusted author");

        let tomb = format!(
            r#"{{"author":"{}","lamport":2,"ops":[{{"op":"remove_state","state":"{a}"}}],"parents":["{a}"],"type":"{DELTA_TYPE}"}}"#,
            k.did()
        );
        let tomb = canon::to_string(&k.sign(&canon::parse(&tomb).unwrap()));
        assert!(
            s.insert_wire(&tomb)
                .unwrap_err()
                .0
                .starts_with("unknown op")
        );

        let forged = wa.replace("/a", "/z");
        assert_eq!(s.insert_wire(&forged).unwrap_err().0, "invalid signature");
        assert_eq!(s.len(), 1);
    }

    #[test]
    fn empty_trust_cannot_open_a_store() {
        assert!(DagStore::new(Config::new(vec![])).is_err());
    }

    #[test]
    fn the_orphan_buffer_is_bounded() {
        let k = Key::from_label("agent");
        let mut cfg = Config::new(vec![k.did()]);
        cfg.max_orphans = 2;
        let mut s = DagStore::new(cfg).unwrap();
        for i in 0..5 {
            let parent = format!("{i:064x}");
            let (_, w) = signed(&k, &[&parent], 2, vec![add_state(&format!("/{i}"))]);
            s.insert_wire(&w).unwrap();
        }
        assert_eq!(s.orphan_count(), 2);
    }

    #[test]
    fn same_set_same_view_in_any_order() {
        let k = Key::from_label("agent");
        let (a, wa) = signed(&k, &[], 1, vec![add_state("/a")]);
        let (b, wb) = signed(&k, &[&a], 2, vec![add_state("/b")]);
        let (_, wc) = signed(&k, &[&b], 3, vec![add_state("/c")]);
        let mut s1 = store(&k);
        let mut s2 = store(&k);
        for w in [&wa, &wb, &wc] {
            s1.insert_wire(w).unwrap();
        }
        for w in [&wc, &wa, &wb] {
            s2.insert_wire(w).unwrap();
        }
        assert_eq!(s1.heads(), s2.heads());
        assert_eq!(
            canon::to_string(&s1.view().to_value()),
            canon::to_string(&s2.view().to_value())
        );
    }
}
