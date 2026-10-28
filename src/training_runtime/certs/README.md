# Internal CA certificates (optional)

Place your organization's **root CA certificate** in this directory if the
training agent must call an internal HTTPS endpoint (for example a self-hosted,
OpenAI-compatible model server) whose certificate is issued by a private CA.
The image build installs it into the container trust store, so TLS verification
stays **on**. Do not work around a private CA by disabling verification.

## What to put here

- One or more certificates in PEM (`.pem`) or `.crt` format.
- The root CA is normally enough: the agent only needs to trust the root of the
  issuing chain. Add a full chain file only if your endpoint requires it.

Verify the fingerprint against the value published by your PKI team before use:

    openssl x509 -fingerprint -sha256 -noout -in <your-root-ca>.pem

Convert DER (binary) certificates to PEM first:

    openssl x509 -inform der -in <your-root-ca>.der -out <your-root-ca>.pem

## How it takes effect

During the image build (see the CA layer in `../Dockerfile`):

1. Every `*.pem` / `*.crt` here is normalized to `.crt`, copied to
   `/usr/local/share/ca-certificates/`, and **appended** to the system trust
   store with `update-ca-certificates`. Public CAs are kept.
2. The same certificates are appended to Python's `certifi` bundle, because
   httpx and the openai SDK read certifi, not the system store.
3. `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` and `CURL_CA_BUNDLE` point at the merged
   system bundle, so requests, httpx, the openai SDK, urllib and curl all trust
   the internal CA while still trusting public CAs.

## Notes

- With no certificate here (only this README), no extra CA is installed and the
  image behaves exactly as before.
- Rebuild the training image after adding or replacing a certificate.
- Do not commit private keys. A CA certificate is public material; its key is not.
