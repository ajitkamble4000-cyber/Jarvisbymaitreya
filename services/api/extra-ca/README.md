Extra trust anchors for image builds.

Put PEM certificates named *.crt here (for example a corporate or intercepting-proxy root CA) before running
docker build. The build appends them to the image's system certificate bundle and points pip and npm at it.
The directory holds nothing by default, so no certificate is trusted beyond the base image's bundle.
Certificates are ignored by git (see .gitignore). Do not commit private keys here.
