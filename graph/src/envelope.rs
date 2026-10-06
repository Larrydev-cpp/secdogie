//! Signed envelopes, mirroring `secdogie_identity.signing`: a signed object is
//! its payload plus `signer` (a did:key) and `sig` (base64 of a detached
//! Ed25519 signature over the canonical bytes of the payload *without* those
//! two keys).
//!
//! Verification is `verify_strict` (no small-order keys, canonical `S`), and the
//! signature is checked before anything else about the signer is believed.

use std::collections::{BTreeMap, BTreeSet};

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD;
use ed25519_dalek::{Signature, VerifyingKey};

use crate::canon::{self, Value};
use crate::did::pubkey_from_did;

/// The set of DIDs whose envelopes are accepted. Zero trust by default: there
/// is no "anyone" -- an empty set cannot be constructed (the same rule as
/// `secdogie_identity.require_trust`).
#[derive(Clone, Debug)]
pub struct Trust {
    dids: BTreeSet<String>,
}

impl Trust {
    pub fn new<I, S>(dids: I) -> Result<Trust, &'static str>
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        let mut set = BTreeSet::new();
        for d in dids {
            let d = d.into();
            pubkey_from_did(&d).map_err(|_| "trusted DID is not an Ed25519 did:key")?;
            set.insert(d);
        }
        if set.is_empty() {
            return Err("no trusted DIDs configured (zero trust: an empty set trusts no one)");
        }
        Ok(Trust { dids: set })
    }

    pub fn contains(&self, did: &str) -> bool {
        self.dids.contains(did)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum EnvelopeError {
    NotAnEnvelope,
    BadSigner,
    BadSignature,
    InvalidSignature,
}

impl EnvelopeError {
    pub fn reason(&self) -> &'static str {
        match self {
            EnvelopeError::NotAnEnvelope => "missing or malformed signer/sig envelope",
            EnvelopeError::BadSigner => "signer is not an Ed25519 did:key",
            EnvelopeError::BadSignature => "sig is not base64 of 64 bytes",
            EnvelopeError::InvalidSignature => "invalid signature",
        }
    }
}

/// A payload whose signature verified. Trust (is the signer allowed?) is the
/// caller's next question, asked only after this succeeds.
#[derive(Clone, Debug)]
pub struct Verified {
    pub signer: String,
    pub payload: Value,
    pub payload_bytes: Vec<u8>,
}

/// Verifies `obj`'s signature over its payload. Does not consult any trust set.
pub fn verify(obj: &BTreeMap<String, Value>) -> Result<Verified, EnvelopeError> {
    let (Some(Value::Str(signer)), Some(Value::Str(sig_b64))) = (obj.get("signer"), obj.get("sig"))
    else {
        return Err(EnvelopeError::NotAnEnvelope);
    };
    let pk = pubkey_from_did(signer).map_err(|_| EnvelopeError::BadSigner)?;
    let sig = STANDARD
        .decode(sig_b64)
        .map_err(|_| EnvelopeError::BadSignature)?;
    let sig: [u8; 64] = sig.try_into().map_err(|_| EnvelopeError::BadSignature)?;
    let key = VerifyingKey::from_bytes(&pk).map_err(|_| EnvelopeError::BadSigner)?;
    let payload: BTreeMap<String, Value> = obj
        .iter()
        .filter(|(k, _)| *k != "signer" && *k != "sig")
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    let payload = Value::Obj(payload);
    let bytes = canon::to_bytes(&payload);
    key.verify_strict(&bytes, &Signature::from_bytes(&sig))
        .map_err(|_| EnvelopeError::InvalidSignature)?;
    Ok(Verified {
        signer: signer.clone(),
        payload,
        payload_bytes: bytes,
    })
}

#[cfg(test)]
pub(crate) mod testkit {
    //! Test-only signing. The library itself never signs: the TS runtime holds
    //! the agent key in WebCrypto.
    use super::*;
    use crate::did::did_from_pubkey;
    use ed25519_dalek::{Signer, SigningKey};

    pub struct Key(pub SigningKey);

    impl Key {
        pub fn from_label(label: &str) -> Key {
            use sha2::Digest;
            let seed: [u8; 32] =
                sha2::Sha256::digest(format!("secdogie/vectors/{label}").as_bytes()).into();
            Key(SigningKey::from_bytes(&seed))
        }

        pub fn did(&self) -> String {
            did_from_pubkey(&self.0.verifying_key().to_bytes())
        }

        pub fn sign(&self, payload: &Value) -> Value {
            let Value::Obj(mut m) = payload.clone() else {
                panic!("payload must be an object")
            };
            let sig = self.0.sign(&canon::to_bytes(payload));
            m.insert("signer".into(), Value::Str(self.did()));
            m.insert("sig".into(), Value::Str(STANDARD.encode(sig.to_bytes())));
            Value::Obj(m)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::testkit::Key;
    use super::*;
    use crate::canon::obj;

    #[test]
    fn verifies_its_own_signature_and_rejects_tampering() {
        let k = Key::from_label("agent");
        let signed = k.sign(&obj([("a", Value::int(1)), ("b", Value::str("x"))]));
        let m = signed.as_obj().unwrap();
        let v = verify(m).unwrap();
        assert_eq!(v.signer, k.did());
        assert_eq!(v.payload_bytes, br#"{"a":1,"b":"x"}"#);

        let mut tampered = m.clone();
        tampered.insert("a".into(), Value::int(2));
        assert_eq!(
            verify(&tampered).unwrap_err(),
            EnvelopeError::InvalidSignature
        );

        let mut no_sig = m.clone();
        no_sig.remove("sig");
        assert_eq!(verify(&no_sig).unwrap_err(), EnvelopeError::NotAnEnvelope);
    }

    #[test]
    fn trust_is_never_empty() {
        assert!(Trust::new(Vec::<String>::new()).is_err());
        assert!(Trust::new(["not-a-did"]).is_err());
        assert!(Trust::new([Key::from_label("agent").did()]).is_ok());
    }
}
