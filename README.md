# M4aToMp3

iTunes などで取り込んだ `.m4a`(MPEG-4 AAC)ファイルを、フォルダ単位でまとめて処理する Windows 用アプリです。

1. ファイル名の最後に付いた連番(`_001`、`_002` など)を削除します。
2. MP3 に変換し、各フォルダの中に作る `変換` サブフォルダに保存します。曲名などのタグとジャケット写真も引き継ぎます。

元の `.m4a` は削除しません。

## 必要なもの

- Windows 10 / 11
- Python 3.11 以上(tkinter 付き。python.org のインストーラーなら標準で入っています)
- ffmpeg(MP3 への変換に使います)

### ffmpeg のインストール

PowerShell で次のコマンドを実行します。

```powershell
winget install --id Gyan.FFmpeg -e --source winget
```

`--source winget` を付けないと、環境によっては msstore ソースの証明書エラー(`0x8a15005e`)で失敗します。

ffmpeg は次の順番で自動的に探します。見つからない場合は、画面の「ffmpeg 設定...」から `ffmpeg.exe` の場所を指定してください。

1. 前回指定した場所
2. PATH
3. アプリと同じフォルダ(`ffmpeg.exe` または `ffmpeg\bin\ffmpeg.exe`)
4. winget のインストール先(`%LOCALAPPDATA%\Microsoft\WinGet\...`)

winget でインストールした直後でも、PATH の反映を待たずに見つけられます。

## 起動方法

```powershell
python app.py
```

## 使い方

1. 「追加...」で対象フォルダを追加します。複数のフォルダを追加できます。
2. オプションを選びます。

   | オプション | 既定値 | 内容 |
   |---|---|---|
   | サブフォルダも処理する | ON | アーティストのフォルダを指定すると、その下のアルバムをすべて処理します |
   | 連番を削除する | ON | |
   | MP3 に変換する | ON | |
   | ビットレート | 320 kbps | 128 / 192 / 256 / 320 kbps(CBR)から選べます |
   | 既存の MP3 を上書きする | OFF | |

3. 「実行前に確認」を押すと、ファイルを変更せずに、リネーム予定・変換予定・スキップ予定をログに表示します。
4. 問題がなければ「実行」を押します。処理中は「中止」で止められます。変換中のファイルの作りかけは削除されます。
5. 終わると、フォルダごとの集計と「要確認」の一覧(スキップ・失敗・警告)が表示されます。

ログは `%APPDATA%\M4aToMp3\logs\` にも保存されます。設定(フォルダ一覧・オプション・ffmpeg の場所)は `%APPDATA%\M4aToMp3\settings.json` に保存され、次回起動時に復元されます。

## 処理のルール

### 対象フォルダ

指定したフォルダ(とサブフォルダ)のうち、`.m4a` を含むフォルダが対象です。出力先の `変換` フォルダは対象外です。

### 連番の削除

- 拡張子を除いたファイル名の最後が `_` + 半角数字3桁(正規表現 `_[0-9]{3}\Z`)のとき、その部分だけを削除します。
- 名前の途中にある `_` や、連番の前にある `_` は残します。

| 元の名前 | 変更後 |
|---|---|
| `アイドル_001.m4a` | `アイドル.m4a` |
| `邂逅_004.m4a` | `邂逅.m4a` |
| `lulu__001.m4a` | `lulu_.m4a` |
| `木星 (feat_稲葉浩志).m4a` | 変更なし |

変更後の名前のファイルがすでにある場合は、リネームしません(上書きは絶対にしません)。

- ログには `SKIP (exists)` と表示されます。
- 2つのファイルの比較(曲名・トラック番号・アルバム・再生時間・サイズ)が「要確認」に表示されます。
- 大文字小文字の違いだけの名前も、同じ名前として扱います。

### MP3 変換

- 出力先は `<フォルダ>\変換\<元の名前>.mp3` です。
- 同じ名前の MP3 がすでにある場合は、スキップします(上書きオプションが OFF のとき)。
- ffmpeg の主な設定:`-c:a libmp3lame -b:a 320k -map_metadata 0 -c:v copy -disposition:v attached_pic -id3v2_version 3 -write_id3v1 1`
  - タグは Windows や多くのプレーヤーで表示しやすい ID3v2.3 で書き込みます。
  - ジャケット写真のコピーに失敗した場合は、JPEG に変換して再試行します。
- 変換後に ffprobe で、ジャケット写真と曲名タグが入っているか確認します。足りない場合は `NO COVER` / `NO TITLE` の警告を出します。
- 一時ファイル(`*.mp3.part`)に書き出してから名前を変えるので、失敗や中止のときに壊れた `.mp3` が残ることはありません。

## exe にする

```powershell
python -m pip install pyinstaller
python -m PyInstaller --onefile --windowed --name M4aToMp3 app.py
```

`dist\M4aToMp3.exe` ができます。ffmpeg は同梱しないので、別途インストールが必要です。`dist` に `ffmpeg.exe` と `ffprobe.exe` を一緒に置けば、インストールしなくても動きます。

### ドラッグ&ドロップ(任意)

`tkinterdnd2` を入れると、エクスプローラーからフォルダをドラッグ&ドロップで追加できるようになります。

```powershell
python -m pip install tkinterdnd2
```

exe にする場合は、PyInstaller のコマンドに `--collect-all tkinterdnd2` を付けてください。

## テスト

連番削除ルールのテストがあります。標準ライブラリだけで実行できます。

```powershell
python -m unittest discover -s tests -t .
```

pytest を入れている場合は、`python -m pytest` でも実行できます。

## ファイル構成

```
app.py                  起動用
m4a2mp3/
  naming.py             連番の判定と削除
  processor.py          フォルダの検索、リネーム、変換、集計(GUI に依存しない)
  ffmpeg_tools.py       ffmpeg / ffprobe の検出と呼び出し
  settings.py           設定の保存と読み込み
  gui.py                画面(tkinter)
tests/test_naming.py    連番削除ルールのテスト
```
