# Temporary reverse-image bridge

Generic Cloudflare Workers + KV deployment template. No personal account IDs, endpoints or upload credentials are included.

1. Create a Worker and a KV namespace, with binding `IMAGES`.
2. Set a Worker secret named `UPLOAD_TOKEN` to a private value. The template rejects uploads when it is unset.
3. Deploy `worker.js` and enter your Worker HTTPS root URL and the same upload token in the local app's image-host settings.

Settings are stored outside the repository in the application home (`~/.hyw-frontier/image-bridge.json` by default), with mode 0600. The upload key is never returned to the model or read back into the settings page. Blank key preserves the stored key only if the endpoint is unchanged. Each user configures their own deployment. The template does not change an already deployed Worker's secret settings automatically.

- `POST /upload`: Bearer token required; accepts raw image bytes, multipart `file`, or JSON `{"url":"https://..."}`.
- `GET/HEAD /images/<sha256>`: public image until next midnight in Asia/Shanghai.
- KV absolute `expiration` removes images daily without a cron scan; reads also check expiry and disable caching.
- Maximum 5 MiB, JPEG/PNG/WebP/GIF. The final 60 seconds before midnight reject new uploads due to KV TTL requirements.
- Importing the Worker's own image URL reuses KV directly.
- The same uploaded URL is shared by the three Reader requests for Yandex, Google Lens and TinEye.

Public image URLs appear in search engine requests and model tool results. Keep local logs and deployment-specific reports out of public source.
