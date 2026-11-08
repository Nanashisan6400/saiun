# Ubuntu版 / Ubuntu version

GTK4・libadwaita・WebKitGTK 6.0・Python/PyGObjectを使用するオフラインEPUBリーダーです。
開発実機はUbuntu 26.04 / GNOME Wayland / Surface Go 3です。全環境での互換性を保証する段階ではありません。

## インストールと起動

Ubuntuのパッケージ管理で実行依存を導入してください。次の操作には管理者権限が必要です。

```sh
sudo apt install python3 python3-gi python3-cairo python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-webkit-6.0 gir1.2-rsvg-2.0
```

ダウンロードしたソースのリポジトリ直下で起動します。

```sh
./ubuntu/ubuntureader/run.sh
```

ユーザーのアプリ一覧へ起動アイコンを登録するには次を実行します。

```sh
python3 ubuntu/ubuntureader/install_launcher.py
```

ソースを移動した場合はアイコンを再登録してください。ライブラリはXDGデータ領域に保存され、ソースと一緒には削除されません。元のEPUBは書き換えません。内蔵IPAex明朝/ゴシックのライセンス全文は`ubuntureader/assets/fonts/`にあります。

## 検査

選別したユニットテストを含みます。書籍や実機の検証結果は同梱していません。

```sh
cd ubuntu
python3 -m unittest ubuntureader.reader.test_library ubuntureader.reader.test_fixed ubuntureader.reader.test_progressive ubuntureader.reader.test_seamless
```

長時間使用のメモリ推移やEPUB全般の互換性は引き続き確認中です。

## English

Install the Ubuntu packages shown above, then run `./ubuntu/ubuntureader/run.sh` from the repository root. Package installation requires administrator privileges; application launcher registration is per-user and does not.

The source uses system GTK/WebKit libraries and includes unmodified IPAex fonts with their license. Your library is stored in the XDG data directory. Original EPUB files are not modified. No books or private reading records are distributed. Long-running memory behavior and broad EPUB compatibility remain under evaluation.
