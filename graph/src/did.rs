//! `did:key` for Ed25519, mirroring `secdogie_identity/did.py`: the multicodec
//! prefix `0xed 0x01` plus the 32-byte public key, base58btc-encoded behind the
//! `z` multibase tag. base58btc is inline, as in Python, rather than a dependency.

const PREFIX: &str = "did:key:z";
const ED25519_MULTICODEC: [u8; 2] = [0xed, 0x01];
const ALPHABET: &[u8; 58] = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
// A did:key for a 32-byte key is 56 characters after the prefix; anything much
// longer is not one, and refusing it early keeps the decode loop bounded.
const MAX_ENCODED: usize = 64;

fn b58_index(c: u8) -> Option<u32> {
    ALPHABET.iter().position(|&a| a == c).map(|p| p as u32)
}

fn b58decode(s: &str) -> Option<Vec<u8>> {
    let mut le: Vec<u8> = Vec::new(); // little-endian base-256 accumulator
    for c in s.bytes() {
        let mut carry = b58_index(c)?;
        for b in le.iter_mut() {
            carry += u32::from(*b) * 58;
            *b = (carry & 0xff) as u8;
            carry >>= 8;
        }
        while carry > 0 {
            le.push((carry & 0xff) as u8);
            carry >>= 8;
        }
    }
    let zeros = s.bytes().take_while(|&c| c == b'1').count();
    le.extend(std::iter::repeat_n(0u8, zeros));
    le.reverse();
    Some(le)
}

fn b58encode(data: &[u8]) -> String {
    let mut digits: Vec<u8> = Vec::new(); // little-endian base-58 digits
    for &byte in data {
        let mut carry = u32::from(byte);
        for d in digits.iter_mut() {
            carry += u32::from(*d) << 8;
            *d = (carry % 58) as u8;
            carry /= 58;
        }
        while carry > 0 {
            digits.push((carry % 58) as u8);
            carry /= 58;
        }
    }
    let zeros = data.iter().take_while(|&&b| b == 0).count();
    let mut out = "1".repeat(zeros);
    out.extend(digits.iter().rev().map(|&d| ALPHABET[d as usize] as char));
    out
}

/// The 32-byte Ed25519 public key a `did:key:z...` names.
pub fn pubkey_from_did(did: &str) -> Result<[u8; 32], &'static str> {
    let Some(enc) = did.strip_prefix(PREFIX) else {
        return Err("not a did:key (base58btc / Ed25519)");
    };
    if enc.is_empty() || enc.len() > MAX_ENCODED {
        return Err("not a did:key (base58btc / Ed25519)");
    }
    let raw = b58decode(enc).ok_or("invalid base58 in did:key")?;
    if raw.len() != 34 || raw[..2] != ED25519_MULTICODEC {
        return Err("did:key is not a 32-byte Ed25519 key");
    }
    let mut pk = [0u8; 32];
    pk.copy_from_slice(&raw[2..]);
    Ok(pk)
}

/// `did:key:z...` for a 32-byte Ed25519 public key.
pub fn did_from_pubkey(pk: &[u8; 32]) -> String {
    let mut raw = Vec::with_capacity(34);
    raw.extend_from_slice(&ED25519_MULTICODEC);
    raw.extend_from_slice(pk);
    format!("{PREFIX}{}", b58encode(&raw))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip() {
        for pk in [[0u8; 32], [0xffu8; 32], core::array::from_fn(|i| i as u8)] {
            let did = did_from_pubkey(&pk);
            assert!(did.starts_with("did:key:z6Mk"), "{did}");
            assert_eq!(pubkey_from_did(&did).unwrap(), pk);
        }
    }

    #[test]
    fn refuses_what_is_not_an_ed25519_did_key() {
        assert!(pubkey_from_did("did:web:example.com").is_err());
        assert!(pubkey_from_did("did:key:z0OIl").is_err()); // not base58
        assert!(pubkey_from_did("did:key:z").is_err());
        assert!(pubkey_from_did(&format!("did:key:z{}", "2".repeat(200))).is_err());
        // a valid base58 string that is not 0xed01 + 32 bytes
        assert!(pubkey_from_did("did:key:z111111").is_err());
    }
}
