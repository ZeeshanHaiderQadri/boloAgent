# Bolo Agent

> 💡 **Notice**: This documentation is community-maintained. If you spot any translation inaccuracies or content that has drifted from the English version, please feel free to open a PR!
>
> 💡 **注記**: このドキュメントはコミュニティによって保守されています。翻訳の不正確さや英語版からの内容のずれを見つけた場合は、ぜひ PR を作成してください。

**オープンソースでセルフホスト可能な Vapi / Retell の代替手段** -- ビジュアルワークフロービルダーで本番向け音声エージェントを構築し、数分でテストし、MCP 経由で AI コーディングアシスタントに設計や編集を任せられます。

<p align="center">
  <a href="https://app.boloagent.com">
    <img src="https://img.shields.io/badge/▶_クラウド版を試す-app.boloagent.com-2563eb?style=for-the-badge" alt="クラウド版を試す">
  </a>
  &nbsp;
  <a href="#-クイックスタート">
    <img src="https://img.shields.io/badge/⚡_60秒でセルフホスト-コマンド1つ-111827?style=for-the-badge" alt="60秒でセルフホスト">
  </a>
  &nbsp;
  <a href="https://github.com/ZeeshanHaiderQadri/bolo/discussions">
    <img src="https://img.shields.io/badge/%F0%9F%92%AC_GitHub_Discussions-0D9488?style=for-the-badge" alt="GitHub Discussions">
  </a>
</p>

<p align="center">
  <a href="https://docs.boloagent.com">📖 ドキュメント</a> &nbsp;·&nbsp;
  <a href="LICENSE">📜 BSD 2-Clause</a> &nbsp;·&nbsp;
  <a href="NOTICE">📄 クレジット表記</a> &nbsp;·&nbsp;
  <a href="README.md">🌐 English</a> &nbsp;·&nbsp;
  <a href="README.zh-CN.md">🌐 中文</a>
</p>

<p align="center">
  <img src="docs/images/hero.gif" alt="Bolo の動作デモ -- ワークフローを構築し、音声エージェントを起動して会話する" width="80%">
</p>

- **100% オープンソース**でセルフホスト可能 -- Vapi や Retell と違い、ベンダーロックインはありません
- **完全な制御と透明性** -- すべてのコードが公開され、LLM / TTS / STT の統合も柔軟に差し替えられます



## ⚖️ Bolo vs Vapi vs Retell

音声 AI プラットフォームを評価しているチームに向けて、重要な観点を率直に比較します。

|  | **Bolo** | **Vapi** | **Retell** |
|---|---|---|---|
| **ライセンス** | BSD 2-Clause (オープンソース) | プロプライエタリ | プロプライエタリ |
| **セルフホスト** | ✅ 可能 -- Docker コマンド 1 つ | ❌ SaaS のみ | ❌ SaaS のみ |
| **料金** | 無料(セルフホスト)・従量課金(クラウド) | 分単位課金の SaaS | 分単位課金の SaaS |
| **独自 LLM / STT / TTS の利用** | ✅ 任意のプロバイダー、または Bolo 標準スタック | 提供範囲内で設定可能 | 提供範囲内で設定可能 |
| **ソースコードレベルのカスタマイズ** | ✅ すべてのコードを自由に変更可能 | ❌ クローズドソース | ❌ クローズドソース |
| **データレジデンシー** | 自社インフラ、自社ルール | ベンダーのクラウド | ベンダーのクラウド |
| **ベンダーロックイン** | なし | あり | あり |


## 🚀 クイックスタート

##### ローカルマシンに Bolo をダウンロードしてセットアップ

> **注記**
> 製品改善のため、匿名の利用状況データを収集します。無効にするには、起動スクリプトを実行する前に `ENABLE_TELEMETRY=false` を設定してください。

> **注記**
> リモートサーバーでプラットフォームを実行したい場合は、[ドキュメント](https://docs.boloagent.com/deployment/docker#option-2:-remote-server-deployment)を参照してください。

```bash
curl -o docker-compose.yaml https://raw.githubusercontent.com/ZeeshanHaiderQadri/bolo/main/docker-compose.yaml && curl -o start_docker.sh https://raw.githubusercontent.com/ZeeshanHaiderQadri/bolo/main/scripts/start_docker.sh && chmod +x start_docker.sh && ./start_docker.sh
```

> **⚡ AI エージェントにセットアップを任せたいですか?**
> **Claude Code** または **Codex** を使っている場合は、公式の [Bolo セットアップ skill](https://github.com/ZeeshanHaiderQadri/bolo-plugins) をインストールすると、インストール、設定、トラブルシューティングをエージェントに任せられます。OS を検出し、適切なデプロイ方法を選び、Bolo 付属のセットアップスクリプトを実行して結果を検証します。
>
> ```text
> # Claude Code の場合
> /plugin marketplace add ZeeshanHaiderQadri/bolo-plugins
> /plugin install bolo@bolo
> ```
>
> その後、新しいセッションを開始して _"set up Bolo"_ と依頼するか、`/bolo-setup` を実行してください。Codex も対応しています。詳しくは[プラグインリポジトリ](https://github.com/ZeeshanHaiderQadri/bolo-plugins#install)を参照してください。

> **注記**
> 初回起動では、すべてのイメージをダウンロードするため 2-3 分かかる場合があります。起動後、http://localhost:3010 を開くと最初の AI 音声アシスタントを作成できます。
> よくある問題と解決策は 🔧 **[トラブルシューティング](docs/getting-started/troubleshooting.mdx)** を参照してください。

### 🎙️ 最初の音声ボット

1. ブラウザで [http://localhost:3010](http://localhost:3010) を開きます。
2. **Inbound(着信)** または **Outbound(発信)** を選び、ボットに名前を付けます(例: _リード判定_)。続けて用途を 5-10 語で説明します(例: _保険フォーム送信者の購入意向を確認_)。
3. **Test Agent** をクリックします。
4. **Test Audio** でブラウザからエージェントと会話するか、**Test Chat** でテキストベースに素早く反復します。Test Chat ではユーザー発話を編集または再実行でき、Bolo がその地点からエージェントの応答とノード遷移を再生成します。

> 🔑 **API キーは不要です。** Bolo には自動生成されるキーと、組み込みの LLM / TTS / STT スタックが付属しています。必要に応じて、独自の LLM、TTS、STT、または Twilio、Vonage、Telnyx などの電話連携プロバイダーをいつでも接続できます。

## MCP でエージェントを構築

Bolo には MCP サーバーが付属しているため、コーディングエージェントが Bolo ワークスペース内で直接作業できます。

Codex、Claude Code、Cursor、または任意の MCP クライアントを接続すると、既存エージェントの確認、Bolo ドキュメントの検索、ノードスキーマの取得、新しいワークフローの作成、自然言語からのドラフト編集保存ができます。

コーディングエージェントに音声エージェントの構築を依頼するときは、1 行のプロンプトだけでなく、ユースケース用の短いスクリプトを共有してください。エージェントのペルソナ、通話フロー、ルール、反論処理、成功基準、可能であればサンプル会話を含めると効果的です。

アシスタントの接続方法は [MCP ガイド](https://docs.boloagent.com/integrations/mcp) を参照してください。

## 機能

### 音声エージェントビルダー

- Start ノード、Agent ノード、グローバル指示、ツール、遷移、通話終了結果を備えたビジュアルワークフロービルダー
- ブラウザ音声テスト用の **Test Audio** と、高速なプロンプト反復用の **Test Chat** を備えた Test Agent パネル
- 本番ワークフロー向けの QA ノード、ナレッジベース、Webhook、埋め込み、ツール呼び出し

### 音声と電話連携

- Twilio、Vonage、Telnyx、Plivo、Vobiz、Cloudonix、Asterisk ARI などの電話連携を標準搭載
- 対応する電話連携プロバイダーでは通話転送による有人対応が可能
- 独自の LLM、TTS、STT、電話連携プロバイダーを接続可能。成果物は同梱の MinIO または AWS / S3 互換ストレージに保存できます

### 開発者体験

- セルフホスト向けの 1 コマンド Docker セットアップ
- カスタマイズしやすい Python バックエンドとモジュラーなプロバイダー構成
- プログラムからのエージェント作成とアウトバウンド通話に対応する Python / Node SDK

## デプロイ方法

### ローカル開発

[ローカルセットアップ](https://docs.boloagent.com/contribution/setup)を参照してください。

### セルフホストデプロイ

リモートサーバーへのデプロイや HTTPS 設定を含む詳しい手順は、[Docker デプロイガイド](https://docs.boloagent.com/deployment/docker#option-2-remote-server-deployment)を参照してください。

### クラウド版

マネージドクラウド版は [https://www.boloagent.com](https://www.boloagent.com/) から利用できます。

## 📚 ドキュメント

完全なドキュメントは [https://docs.boloagent.com](https://docs.boloagent.com/) を参照してください。

## 📦 SDKs

- **Python SDK** -- [pypi.org/project/bolo-sdk](https://pypi.org/project/bolo-sdk/)
- **Node SDK** -- [npmjs.com/package/@bolo/sdk](https://www.npmjs.com/package/@bolo/sdk)

## 🤝 コミュニティとサポート

- **GitHub Discussions** -- ユースケースを共有し、質問し、ワークフローのレシピを交換できます。
- **GitHub Issues** -- バグ報告や機能リクエストに利用してください。

👉 参加はこちら → [GitHub Discussions](https://github.com/ZeeshanHaiderQadri/bolo/discussions)

## 🙌 コントリビューション

コントリビューションを歓迎します。Bolo Agent は 100% オープンソースであり、今後もそうあり続けます。

### はじめに

- このリポジトリを Fork する
- 機能ブランチを作成する(`git checkout -b feature/AmazingFeature`)
- 変更をコミットする(`git commit -m 'Add some AmazingFeature'`)
- ブランチへプッシュする(`git push origin feature/AmazingFeature`)
- Pull Request を作成する

## ⭐ Star 履歴

<img src="docs/images/star-history.png" alt="Bolo star history" width="80%">

## 🙏 謝辞

Bolo は、オープンソースの音声 AI プラットフォーム [Dograh](https://github.com/dograh-hq/dograh) を基盤として始まりました。基盤を提供してくださった Dograh チームとコントリビューターに感謝します。BSD 2-Clause の原著作者の著作権表示は [LICENSE](LICENSE) と [NOTICE](NOTICE) に保持されています。

## 📄 ライセンス

Bolo Agent は [BSD 2-Clause License](LICENSE) のもとで公開されています。

BSD 2-Clause は商業利用・改変・再配布を明示的に許諾しており、原著作者の著作権表示・条件・免責条項は [LICENSE](LICENSE) と [NOTICE](NOTICE) にそのまま保持されています。本リポジトリはその条件を満たしています。

## 🏢 私たちについて

**Bolo Agent** が ❤️ を込めて開発しています。
Bolo はオープンソースの音声 AI プラットフォームを基盤としています。技術的な詳細と帰属情報は [NOTICE](NOTICE) に記載しています。

<br><br><br>

  <p align="center">
    <a href="https://github.com/ZeeshanHaiderQadri/bolo">⭐ GitHub で Star する</a> |
    <a href="https://app.boloagent.com">☁️ クラウド版を試す</a> |
    <a href="https://github.com/ZeeshanHaiderQadri/bolo/discussions">💬 </a>
  </p>
