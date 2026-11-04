# Release signing keys

Cloud KMS, project `flexible-vision-staging`, location `us-central1`,
keyring `onprem-release-signing`. HSM protection, `EC_SIGN_P256_SHA256`,
90-day destroy-scheduled-duration.

Neither the keyring nor the keys can be deleted; only versions can be
destroyed, and that is reversible for 90 days via `gcloud kms keys versions
restore`.

| key | fingerprint | role |
|---|---|---|
| `release-signing` | `e7b6ec363b2142c5` | active - signs every release |
| `release-signing-standby` | `e0a15f7c652d6c23` | never used until a rotation |

Fingerprints are the first 16 hex of SHA-256 over the DER SubjectPublicKeyInfo,
which is what `release/trust.py` reports and what `/releases` shows per device.

## Signing

    python -m release.cut --from-stable \
      --key gcpkms://projects/flexible-vision-staging/locations/us-central1/keyRings/onprem-release-signing/cryptoKeys/release-signing/versions/1

## Fetching a public key to provision

    gcloud kms keys versions get-public-key 1 \
      --location us-central1 --keyring onprem-release-signing \
      --key release-signing --project flexible-vision-staging \
      --output-file release-signing.pem

Both public keys belong in every device's trust store at install, so a
rotation is "start signing with the standby" and needs no trust update.

## Who can sign

`roles/editor` does NOT grant `cloudkms.cryptoKeyVersions.useToSign`, so the
project's service accounts cannot sign. `roles/owner` DOES, and key-level IAM
cannot override it - so every project Owner can sign with both keys. Narrowing
that means fewer Owners, or moving a key to another project.

Data-access audit logging is enabled for `cloudkms.googleapis.com` on this
project, so every signature is recorded with the principal who made it.
