Extra trust anchors are supplied as BuildKit secrets so they do not remain in image layers.

For a network that requires a proxy CA, run the web build from the repository root with:

```sh
docker build --secret id=proxy_ca,src=/path/to/proxy-ca.pem -t jarvis-ai-web apps/web
```

Omit `--secret` on an ordinary network. TLS verification remains enabled in both cases. Never commit a
session-specific certificate or private key to this directory.
