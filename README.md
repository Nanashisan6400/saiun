# 彩雲 / Saiun

彩雲は、作者自身が快適に電子書籍を読むために開発しているEPUBリーダーです。軽快な動作とシンプルな操作性、快適な読書体験を最優先にしています。

表示名は日本語環境では「彩雲」、それ以外では「Saiun」です。

**[スクリーンショット付き取扱説明書](docs/USER_GUIDE.ja.md)** — インストール、本棚、読書操作、目次・しおり、文字設定、削除までを説明しています。

## 対応環境

現在はUbuntu版を開発中です。GTK4・libadwaita・WebKitGTKを使用し、縦書き/横書き、ルビ、章改ページ、目次、しおり、読書位置の保存、見開き、文字設定などに対応します。開発・確認環境はSurface Go 3のUbuntu/Waylandです。詳細と起動方法は[Ubuntu版](ubuntu/README.md)をご覧ください。

iPad版は将来の展開を想定していますが、現在は含まれていません。

## 開発方針

- 最優先事項は軽快な動作と快適な読書体験です。
- MITライセンスに従ったFork・改変・再配布は自由です。著作権表示と許諾文を保持してください。
- **Pull Requestは受け付けません。** 独自の機能が必要な場合はForkして開発してください。
- 不具合報告のIssueは歓迎します。
- 機能追加や仕様変更は、作者自身が必要と判断したものだけを行います。
- 他者からの要望によって機能を増やすことを目的としません。

[開発方針の詳細](CONTRIBUTING.md)を確認してください。報告時は環境・再現手順・期待する動作・実際の動作を記載し、書籍本文や個人情報を無断で添付しないでください。

## ライセンス

本体コード・独自素材・資料は[MIT License](LICENSE)です。内蔵のIPAexフォントや依存ライブラリにはそれぞれのライセンスが適用されます。[第三者素材の表示](THIRD_PARTY_NOTICES.md)を参照してください。書籍・表紙・読書記録は同梱しません。

---

## English

Saiun is an EPUB reader developed for the author's own comfortable reading. Lightweight performance, simple controls, and a comfortable reading experience are the highest priorities.

The application is named **彩雲** in Japanese environments and **Saiun** in other environments.

**[Illustrated user guide (Japanese)](docs/USER_GUIDE.ja.md)**

### Platforms

The Ubuntu version is currently in development, using GTK4, libadwaita, and WebKitGTK. It supports vertical and horizontal text, ruby annotations, chapter page breaks, a table of contents, bookmarks, saved reading positions, two-page spreads, and typography settings. Development testing uses Ubuntu/Wayland on a Surface Go 3. See [Ubuntu instructions](ubuntu/README.md).

An iPad version is a future possibility; no iPad implementation is included yet.

### Development policy

- Lightweight performance and a comfortable reading experience come first.
- Forking, modification, and redistribution are welcome under the MIT License. Preserve the copyright and permission notices.
- **Pull requests are not accepted.** Please develop custom features in a fork.
- Bug reports through Issues are welcome.
- Features and specification changes are added only when the author considers them necessary for the author's own use.
- Expanding the feature set in response to other people's requests is not a project goal.

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports should include your environment, reproduction steps, expected behavior, and actual behavior. Do not attach book content or personal information without permission.

### License and publication

Application code, original assets, and documentation are under the [MIT License](LICENSE). Bundled IPAex fonts and dependencies retain their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md). Books, covers, and reading records are not included.

GitHub hosts published application source snapshots. Publication tools and private development records are not included.
