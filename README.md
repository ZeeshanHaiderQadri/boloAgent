# Bolo Agent

<p align="center">
  <img src="ui/public/bolo-logo.png" alt="Bolo Agent" height="72">
</p>

<p align="center">
  <b>The open-source, self-hostable voice AI platform for inbound &amp; outbound calling</b>
</p>

<p align="center">
  <a href="#-get-started">
    <img src="https://img.shields.io/badge/%E2%9A%A1_Self--host_in_60s-One_command-0F766E?style=for-the-badge" alt="Self-host in 60s">
  </a>
  &nbsp;
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/%F0%9F%93%9C_BSD_2--Clause-0D9488?style=for-the-badge" alt="BSD 2-Clause">
  </a>
</p>

<p align="center">
  <a href="docs">📖 Documentation</a> &nbsp;·&nbsp;
  <a href="LICENSE">📜 BSD 2-Clause</a> &nbsp;·&nbsp;
  <a href="NOTICE">📄 Attribution</a> &nbsp;·&nbsp;
  <a href="README.zh-CN.md">🌐 中文</a> &nbsp;·&nbsp;
  <a href="README.ja-JP.md">🌐 日本語</a>
</p>

Bolo Agent is a production-grade, self-hostable voice AI platform. It lets you build,
test and ship conversational voice agents for **inbound and outbound calling** with a
visual workflow builder, a provider-agnostic LLM/STT/TTS stack, MCP-native tooling, and
built-in telephony.

Everything runs on your infrastructure, with your own API keys.

- **100% open source**, self-hostable — no vendor lock-in
- **Full control & transparency** — every line of code is yours to modify
- **Bring your own stack** — any LLM, STT, TTS, and telephony provider
- **Teal Green brand** — Bolo's own identity, palette and generated artwork (see [Bolo customisations](#-bolo-customisations))

---

## 🎯 What you can build

### Voice Agent Builder

- Visual workflow builder with start nodes, agent nodes, global instructions, tools, transitions, and end-call outcomes
- Test Agent panel with **Test Audio** for browser voice testing and **Test Chat** for fast prompt iteration
- QA nodes, knowledge bases, webhooks, embeds, and tool calling for production workflows

### Inbound & outbound calling

- Built-in telephony integrations: Twilio, Vonage, Telnyx, Plivo, Vobiz, Cloudonix, and Asterisk ARI
- Inbound webhooks and outbound campaign orchestration
- Human handoff with call transfer on supported telephony providers
- Bring your own LLM, TTS, STT, and telephony providers; store artifacts in bundled MinIO or any AWS/S3-compatible storage

### Developer Experience

- One-command Docker setup for self-hosting
- Python backend with a modular provider architecture for customization
- Python and Node SDKs for programmatic agent creation and outbound calls
- MCP server, so coding agents can build and edit workflows in your workspace

---

## 🚀 Get Started

### Run Bolo on your local machine

> **Note**
> Anonymous usage data is collected to improve the product. Opt out by setting
> `ENABLE_TELEMETRY=false` before running the startup script.

> **Note**
> To run on a remote server instead, see the
> [deployment documentation](docs/deployment/docker.mdx).

```bash
curl -o docker-compose.yaml https://raw.githubusercontent.com/ZeeshanHaiderQadri/bolo/main/docker-compose.yaml && curl -o start_docker.sh https://raw.githubusercontent.com/ZeeshanHaiderQadri/bolo/main/scripts/start_docker.sh && chmod +x start_docker.sh && ./start_docker.sh
```

> **Note**
> The quickstart starts a public `*.trycloudflare.com` quick tunnel to the API (for inbound
> telephony webhooks), and signup is enabled. After creating your first account, close
> signup with `ENABLE_SIGNUP=false` in `.env` and re-run `./start_docker.sh`.

> **⚡ Prefer an AI agent to set it up for you?**
> Bolo ships with an official setup skill for Claude Code and Codex that detects your OS,
> picks the right deploy path, runs the setup scripts, and verifies the result.

> **Note**
> First startup may take 2–3 minutes to download all images. Once running, open
> http://localhost:3010 to create your first AI voice assistant!

### 🎙️ Your first voice bot

1. Open [http://localhost:3010](http://localhost:3010) in your browser.
2. Pick **Inbound** or **Outbound**, name your bot (e.g. _Lead Qualification_), and describe the use case in 5–10 words (e.g. _Screen insurance form submissions for purchase intent_).
3. Click **Test Agent**.
4. Use **Test Audio** to talk to your agent in the browser, or **Test Chat** to iterate faster in text. In Test Chat you can edit or replay user turns, and Bolo regenerates the agent's replies and node transitions from that point.

> 🔑 **No API keys needed to start.** Bolo ships with auto-generated keys and its own
> LLM / TTS / STT stack. Connect your own keys for LLM, TTS, STT, or Telephony
> (e.g. Twilio, Vonage, Telnyx) at any time.

---

## 🔌 Build agents with MCP

Bolo ships with an MCP server, so coding agents can work directly inside your workspace.
Connect Codex, Claude Code, Cursor, or any MCP client to inspect existing agents, search
the docs, fetch node schemas, create workflows, and save draft edits from natural language.

When asking your coding agent to build a voice agent, share a short script for the use
case instead of only a one-line prompt: include the agent persona, call flow, rules,
objection handling, success criteria, and a sample conversation if you have one.

---

## 📦 SDKs

| SDK | Package |
|---|---|
| Python | [`bolo-sdk`](sdk/python) — `pip install bolo-sdk` |
| Node | [`@bolo/sdk`](sdk/typescript) — `npm install @bolo/sdk` |

Both SDKs are generated from the public OpenAPI schema and are re-generated with
`scripts/generate_sdk.sh`.

---

## ☁️ Deployment

| Mode | Guide |
|---|---|
| Local development | [docs/contribution/setup.mdx](docs/contribution/setup.mdx) |
| Docker (self-hosted) | [docs/deployment/docker.mdx](docs/deployment/docker.mdx) |
| Helm chart | [deploy/helm/bolo](deploy/helm/bolo) |
| Hostinger | [deploy/hostinger](deploy/hostinger) |

---

## 🎨 Bolo customisations

Bolo is not a verbatim copy — the following changes are part of this fork:

- **Identity.** Every product name, package name, env-var prefix (`BOLO_*`), Docker image
  name, Helm chart, route prefix, SDK package and URL uses the Bolo name.
- **Teal Green design system.** The UI theme (`ui/src/app/globals.css`) is re-based on a
  teal-green palette for both light and dark mode, including the CTA accent, focus rings,
  charts, sidebar and surface tints. The Mintlify docs palette matches.
- **Brand artwork.** The logo, mark, inverse lockup, watermark imprint and favicon are
  original Bolo assets, generated from scratch by
  [`scripts/brand/generate_brand_assets.py`](scripts/brand/generate_brand_assets.py).
  Re-run it to regenerate them after a palette change.

```bash
python3 scripts/brand/generate_brand_assets.py
```

---

## 📄 License & attribution

Bolo Agent is licensed under the [BSD 2-Clause License](LICENSE).

The BSD 2-Clause license explicitly permits commercial use, modification and
redistribution, including in proprietary products, provided the original copyright
notice is preserved. That notice, the license conditions and disclaimer, and
third-party attribution are retained verbatim in [LICENSE](LICENSE) and
[NOTICE](NOTICE) — which this repository does.

Please review [NOTICE](NOTICE) before redistributing, and keep the attribution in your
own documentation.

---

## 🙌 Contributing

We love contributions! Bolo is 100% open source and we intend to keep it that way.

- Fork the repository
- Create your feature branch (`git checkout -b feature/amazing-feature`)
- Commit your changes (`git commit -m 'Add some amazing feature'`)
- Push to the branch (`git push origin feature/amazing-feature`)
- Open a Pull Request

---

## 🌐 Translations

- [English](README.md) — canonical
- [简体中文](README.zh-CN.md)
- [日本語](README.ja-JP.md)

---

<p align="center">
  <a href="https://github.com/ZeeshanHaiderQadri/bolo">⭐ Star us on GitHub</a>
</p>
