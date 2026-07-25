# 入試問題集リファレンスアプリ

入試問題集PDF(単元別ではなく、大学ごとの過去問がまとまった問題集)をアップロードすると、
大学・日程ごとの大問を自動検出し、単元・問題数・大学名で検索して参照できるアプリです。

## 前提とするPDFレイアウト

以下のような、大学の過去問集によくある形式を想定しています。

- 1エントリ = 1大学・1日程分の過去問。ページ上部に大学名の見出しと「試験日 20XX年X月X日」の行がある。
- 問題文ページには、大問ごとに黒背景・白文字の正方形の番号アイコン(1, 2, 3...)が左マージンに振られている。
- 解答ページにも同様の番号アイコンがあり、各小問の解説冒頭に「【単元名】」のようなタグが付いている。

このレイアウトから外れるPDF(番号アイコンが無い、単元タグが無いなど)では、
大問の自動分割や単元タグの自動抽出の精度が下がります。

## 技術的な注意点: OCRベースの解析

このアプリが対象とするPDFの多くは、文字がアウトライン化(ベクター化)されており、
通常のテキストレイヤーを持ちません。そのため以下のパイプラインで解析します。

1. `PyMuPDF` でページを画像化する。
2. `Tesseract OCR`(日本語)でテキストを読み取る。
3. 大問番号アイコンは、OCRでは正しく読み取れない(白文字/黒背景のため)ので、
   `numpy`/`scipy` による画像処理(黒い正方形ブロックの検出)で位置を特定する。
4. 解答ページの「【単元名】」タグをOCRテキストから抽出し、`units.py` の辞書と
   照合して単元を自動タグ付けする。

OCRには誤読が付き物です。特に数式(分数・ルート・添字など)は誤読が多くなりますが、
検索には使わないため実用上の影響は小さいです。一方で大学名・年度・科目・単元タグは
検索の核となる情報なので、アップロード後に「検出結果の確認・修正」タブで
内容を確認し、必要に応じて手動で修正できるようにしています。

## 構成

- バックエンド: FastAPI (`backend/`)
  - `pdf_processor.py`: OCRと画像処理でPDFを大問単位に分割し、大学名・年度・科目・
    単元タグを抽出する
  - `units.py`: 単元名と同義語・関連キーワードの辞書(簡易分類・検索用)
  - `storage.py`: 問題メタデータをJSONファイルで永続化
  - `main.py`: アップロード(非同期処理)・検索・一覧・修正・削除のAPI
- フロントエンド: 素のHTML/CSS/JS (`frontend/`)
- データ: `data/uploads`(元PDF), `data/images`(切り出し画像), `data/index.json`(メタデータ)

## セットアップ

Tesseract OCR(日本語データ含む)が必要です。

### Debian/Ubuntu

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-jpn

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### macOS

```bash
brew install tesseract tesseract-lang  # 日本語データを含む全言語パックが入る

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Windows

1. [UB-Mannheim版Tesseractインストーラー](https://github.com/UB-Mannheim/tesseract/wiki)をダウンロードして実行する。
   インストール中の「Additional language data」の選択画面で **Japanese** に必ずチェックを入れる
   (デフォルトでは英語のみで日本語データが入らない)。
2. インストール先(既定では `C:\Program Files\Tesseract-OCR\tesseract.exe`)を確認する。
3. `backend` フォルダに `.env` ファイルを作るか、起動前に環境変数を設定して
   tesseract の場所をアプリに伝える(PATHを編集しなくても動くようにしてあります)。

   PowerShellの場合:
   ```powershell
   $env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
   ```
   コマンドプロンプトの場合:
   ```cmd
   set TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
   ```
   (毎回設定するのが面倒な場合は、システム環境変数として `TESSERACT_CMD` を登録すれば恒久的に反映されます。)

4. Python環境をセットアップする。
   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

#### 管理者権限が無く、上記インストーラーも実行できない場合(Miniconda経由)

UB-Mannheim版インストーラーで「Install for all users」のチェックを外せば個人フォルダに
インストールでき、通常はこれで管理者権限は不要です。それでも権限エラーになる場合は、
Minicondaを使うと環境ごとインストールできます。

1. [Miniconda](https://docs.conda.io/en/latest/miniconda.html)をダウンロードし、
   インストール時に「Just Me (recommended)」を選択する(個人フォルダに入るため管理者権限不要)。
2. 「Anaconda Prompt (Miniconda3)」を開き、専用の環境を作ってTesseractを入れる。
   ```
   conda create -n nyushimondai python=3.11
   conda activate nyushimondai
   conda install -c conda-forge tesseract
   ```
3. conda-forge版は日本語データを含まないことが多いため、
   [tesseract公式tessdataリポジトリ](https://github.com/tesseract-ocr/tessdata)から
   `jpn.traineddata` をダウンロードし、環境内の `tessdata` フォルダ
   (例: `%USERPROFILE%\miniconda3\envs\nyushimondai\share\tessdata\`)に置く。
4. `where tesseract` で実行ファイルの場所を確認し、`TESSERACT_CMD` に設定する
   (例: `%USERPROFILE%\miniconda3\envs\nyushimondai\Library\bin\tesseract.exe`)。
5. 同じconda環境にアプリの依存関係もインストールして起動する。
   ```
   pip install -r requirements.txt
   $env:TESSERACT_CMD = "上で確認したパス"
   cd backend
   uvicorn main:app --reload
   ```

## 起動

```bash
cd backend
uvicorn main:app --reload
```

ブラウザで http://127.0.0.1:8000 を開きます。

## 使い方

1. 「PDFアップロード」タブで入試問題集PDFを選択します(複数可)。
   大学名・年度・科目・単元タグはすべてOCRで自動抽出され、バックグラウンドで処理されます
   (ページ数が多いPDFは数分かかることがあります)。
2. 「検出結果の確認・修正」タブで、検出された大問ごとの画像と自動抽出されたメタデータを
   確認できます。OCRの誤読があれば、大学名・年度・科目・単元タグをその場で修正して保存できます。
3. 「問題を探す」タブで単元名(例: 微分・積分、確率、二次関数など)、問題数、
   大学名(任意)、科目(任意)を指定して検索すると、該当する問題がページ画像付きで表示されます。
   表示されるのは問題文のみで、解答は表示されません。

## 単元の判定方法

2段構えです。

1. 解答ページの「【単元名】」タグをOCRで読み取り、`units.py` の辞書(キー・同義語)と
   部分一致・あいまい一致(`difflib`)で照合して構造化タグを付ける(信頼度が高い)。
2. 検索時は上記タグに加えて、問題文・解答文全体に対するキーワード全文検索も行う。
   これにより、タグの抽出に失敗した場合でも本文中の関連語句で拾える可能性がある。

辞書にない単元名を入力した場合は、その文字列自体をキーワードとして全文検索するため、
数学以外の科目でも任意のキーワードで検索できます。辞書は `units.py` の `UNIT_KEYWORDS`
に追記することで拡張できます。

## 既知の制約

- 大問番号アイコンのサイズ・位置は publisher(出版社)ごとにレイアウトが異なるため、
  `pdf_processor.py` 内の検出パラメータ(サイズ・塗りつぶし率・マージン)は
  今回確認したサンプルに合わせて調整されています。他のレイアウトでは検出精度が
  下がる可能性があります。
- 解答ページが3段組み以上になっている場合は、2段組みを前提とした読み順の推定が
  崩れる可能性があります。
- OCRの都合上、大学名・年度・科目・単元タグの自動抽出は完全ではありません。
  「検出結果の確認・修正」タブで必ず内容を確認することを推奨します。
- JSONファイルによる永続化のため、大規模なデータ量(数千問など)には向きません。
