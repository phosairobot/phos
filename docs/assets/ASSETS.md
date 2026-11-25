# Site asset sources

`img/neon_phos_logo.png` at the repository root is the sole branding source.
Keep this original PNG unchanged; all published and packaged logo images are
resized derivatives. Do not edit a derivative independently or substitute it for
the source.

| Destination | Dimensions | Purpose |
| --- | --- | --- |
| `docs/assets/images/neon_phos_logo.png` | 512×512 | Website header and landing page |
| `docs/assets/images/neon_phos_logo-favicon.png` | 64×64 | Website favicon |
| `docs/assets/images/neon_phos_logo-touch.png` | 180×180 | Website touch icon |
| `docs/assets/images/neon_phos_logo-social.png` | 1200×630 | Open Graph and Twitter share image |
| `src/robot/web/static/images/neon_phos_logo.png` | 780×780 | Admin login |
| `src/robot/web/static/images/neon_phos_logo-224.png` | 224×224 | Admin dashboard |
| `src/robot/web/static/images/neon_phos_logo-96.png` | 96×96 | Admin navigation |
| `src/robot/web/static/images/neon_phos_logo-favicon.png` | 64×64 | Admin and API-documentation favicon |
| `src/robot/web/static/images/neon_phos_logo-touch.png` | 180×180 | Admin touch icon |
| `src/robot/assets/images/neon_phos_logo-startup-800x600.png` | 800×600 | Pre-rendered startup splash |

MkDocs publishes only documentation assets; the installed Python package bundles
the Admin and startup assets separately. The README uses the original source.

Regenerate every derivative from the repository root on macOS using the built-in
`sips` utility:

```bash
python3 scripts/generate_branding_assets.py
```

The generator uses proportional resizing. Rectangular share and startup images
retain the complete square logo, centered with `#02050D` padding; no logo content
is cropped, stretched, recolored, or redrawn. PNG output preserves lossless
storage. Asset generation is a development operation and adds no Raspberry Pi
runtime dependency. Normal animated eyes and visual expressions are independent
of these branding assets.
