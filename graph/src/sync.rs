//! have/want anti-entropy over the DAG -- transport-neutral, like
//! `citadel/sync.py` for the journal.
//!
//! ```text
//! A -> B  graph_have   {heads}                 what A's frontier is
//! B -> A  graph_want   {cids, have_heads}      the heads B lacks, plus B's own frontier
//! A -> B  graph_deltas {envelopes}             the wanted nodes and every ancestor of
//!                                              theirs not under B's frontier, parents first
//! ```
//!
//! Sending the requester's frontier with the want lets the responder ship the
//! whole missing history in one batch in the common case, instead of one round
//! per generation. Anything still missing after ingest (a batch cap, a peer
//! that knows less than claimed) comes back as a new want for the missing
//! parents. Duplicates are harmless -- insert is idempotent -- so the exchange
//! converges under loss, reordering and repetition. Peer authentication is the
//! transport's job (WebRTC DTLS / the DID-bound UDP session); every delta is
//! still verified on its own by `DagStore::insert`.

use std::collections::BTreeSet;

use crate::canon::{Value, obj, str_arr};
use crate::dag::{DagStore, Outcome};

pub const HAVE: &str = "graph_have";
pub const WANT: &str = "graph_want";
pub const DELTAS: &str = "graph_deltas";
pub const MAX_BATCH: usize = 256;
pub const MAX_WANT: usize = 256;

pub fn have_message(store: &DagStore) -> Value {
    obj([
        ("kind", Value::str(HAVE)),
        ("heads", str_arr(store.heads())),
    ])
}

/// The heads a peer advertised that this store neither holds nor waits on.
pub fn wants_for(store: &DagStore, remote_heads: &[String]) -> Vec<String> {
    let mut want: Vec<String> = remote_heads
        .iter()
        .filter(|c| !store.knows(c))
        .cloned()
        .collect();
    want.sort();
    want.dedup();
    want.truncate(MAX_WANT);
    want
}

fn want_message(store: &DagStore, cids: Vec<String>) -> Value {
    obj([
        ("kind", Value::str(WANT)),
        ("cids", str_arr(cids)),
        ("have_heads", str_arr(store.heads())),
    ])
}

/// Answer to a peer's have: a want, or `None` when nothing is missing.
pub fn on_have(store: &DagStore, remote_heads: &[String]) -> Option<Value> {
    let want = wants_for(store, remote_heads);
    if want.is_empty() {
        None
    } else {
        Some(want_message(store, want))
    }
}

/// The envelopes (canonical text) answering a want, parents first, at most
/// `MAX_BATCH`.
pub fn on_want(store: &DagStore, cids: &[String], their_heads: &[String]) -> Vec<String> {
    let theirs = store.ancestors(their_heads);
    let mut send: BTreeSet<String> = BTreeSet::new();
    for c in cids.iter().take(MAX_WANT) {
        for a in store.ancestors(std::slice::from_ref(c)) {
            if !theirs.contains(&a) {
                send.insert(a);
            }
        }
    }
    store
        .topo(send.iter())
        .into_iter()
        .take(MAX_BATCH)
        .map(|n| n.wire.clone())
        .collect()
}

pub fn deltas_message(envelopes: Vec<String>) -> Value {
    obj([
        ("kind", Value::str(DELTAS)),
        (
            "envelopes",
            Value::Arr(envelopes.into_iter().map(Value::Str).collect()),
        ),
    ])
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct IngestReport {
    pub inserted: Vec<String>,
    pub duplicate: usize,
    pub pending: Vec<String>,
    pub rejected: Vec<(usize, String)>,
}

/// Ingests a batch. Returns the report and, if parents are still missing, the
/// want to send back.
pub fn on_deltas(store: &mut DagStore, envelopes: &[String]) -> (IngestReport, Option<Value>) {
    let mut rep = IngestReport::default();
    for (i, w) in envelopes.iter().take(MAX_BATCH).enumerate() {
        match store.insert_wire(w) {
            Ok(Outcome::Inserted {
                cid,
                promoted,
                dropped,
            }) => {
                rep.inserted.push(cid);
                rep.inserted.extend(promoted);
                rep.rejected
                    .extend(dropped.into_iter().map(|(_, r)| (i, r)));
            }
            Ok(Outcome::Duplicate { .. }) => rep.duplicate += 1,
            Ok(Outcome::Pending { cid, .. }) => rep.pending.push(cid),
            Err(e) => rep.rejected.push((i, e.0)),
        }
    }
    rep.pending.retain(|c| !store.contains(c));
    let missing = wants_for(store, &store.missing_parents());
    let want = if missing.is_empty() {
        None
    } else {
        Some(want_message(store, missing))
    };
    (rep, want)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::canon;
    use crate::dag::tests::{add_state, signed, store};
    use crate::envelope::testkit::Key;

    fn strs(v: &Value, k: &str) -> Vec<String> {
        v.as_obj().unwrap()[k]
            .as_arr()
            .unwrap()
            .iter()
            .map(|x| x.as_str().unwrap().to_string())
            .collect()
    }

    /// `src` advertises its heads to `dst`; `dst` wants, `src` answers. Returns
    /// whether anything new reached `dst`.
    fn pull(dst: &mut DagStore, src: &DagStore) -> bool {
        let Some(want) = on_have(dst, &src.heads()) else {
            return false;
        };
        let env = on_want(src, &strs(&want, "cids"), &strs(&want, "have_heads"));
        !on_deltas(dst, &env).0.inserted.is_empty()
    }

    /// Exchanges in both directions until a round moves nothing.
    fn reconcile(a: &mut DagStore, b: &mut DagStore) -> usize {
        let mut rounds = 0;
        loop {
            rounds += 1;
            assert!(rounds < 10, "did not converge");
            let moved_b = pull(b, a);
            let moved_a = pull(a, b);
            if !moved_a && !moved_b {
                return rounds;
            }
        }
    }

    #[test]
    fn two_stores_converge_in_one_exchange() {
        let k = Key::from_label("agent");
        let mut a = store(&k);
        let mut b = store(&k);
        let (r, wr) = signed(&k, &[], 1, vec![add_state("/r")]);
        a.insert_wire(&wr).unwrap();
        b.insert_wire(&wr).unwrap();
        // a grows a chain of 5, b a chain of 3, both from r
        let mut prev = r.clone();
        for i in 0..5 {
            let (c, w) = signed(&k, &[&prev], 2 + i, vec![add_state(&format!("/a{i}"))]);
            a.insert_wire(&w).unwrap();
            prev = c;
        }
        let mut prev = r;
        for i in 0..3 {
            let (c, w) = signed(&k, &[&prev], 2 + i, vec![add_state(&format!("/b{i}"))]);
            b.insert_wire(&w).unwrap();
            prev = c;
        }
        assert_eq!(reconcile(&mut a, &mut b), 2); // one exchange that moves data, one that confirms
        assert_eq!(a.heads(), b.heads());
        assert_eq!(a.heads().len(), 2);
        assert_eq!(a.len(), 9);
        assert_eq!(
            canon::to_string(&a.view().to_value()),
            canon::to_string(&b.view().to_value())
        );
    }

    #[test]
    fn a_partial_batch_asks_again_for_missing_parents() {
        let k = Key::from_label("agent");
        let mut a = store(&k);
        let mut b = store(&k);
        let (x, wx) = signed(&k, &[], 1, vec![add_state("/x")]);
        let (_, wy) = signed(&k, &[&x], 2, vec![add_state("/y")]);
        a.insert_wire(&wx).unwrap();
        a.insert_wire(&wy).unwrap();
        // b receives only the child (as if the parent's datagram was lost)
        let (rep, want) = on_deltas(&mut b, std::slice::from_ref(&wy));
        assert_eq!(rep.pending.len(), 1);
        let want = want.expect("asks for the missing parent");
        assert_eq!(strs(&want, "cids"), vec![x.clone()]);
        let env = on_want(&a, &strs(&want, "cids"), &strs(&want, "have_heads"));
        let (rep, want) = on_deltas(&mut b, &env);
        assert_eq!(rep.inserted.len(), 2); // x, and y promoted
        assert!(want.is_none());
        assert_eq!(a.heads(), b.heads());
    }

    #[test]
    fn nothing_to_want_when_in_sync() {
        let k = Key::from_label("agent");
        let mut a = store(&k);
        let (_, w) = signed(&k, &[], 1, vec![add_state("/x")]);
        a.insert_wire(&w).unwrap();
        assert!(on_have(&a, &a.heads()).is_none());
        assert_eq!(strs(&have_message(&a), "heads"), a.heads());
    }
}
