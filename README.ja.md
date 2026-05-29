# ableton-strip-silence

`ableton-strip-silence` は、Ableton Live のエクスポートを処理し、結果を `.als` プロジェクトに書き戻す Python CLI ツールです。

以下のエンドツーエンドワークフローを提供します。

1. `.als` のトラック順序/グループ階層に基づいて、エクスポートされた WAV ファイルをマッチングおよび名前変更
2. 無音部分を検出し、オーディオファイルから削除
3. タイムコード付きクリップを Ableton `.als` ファイルに復元

## 機能

- Ableton `.als` + エクスポート WAV ファイルに対応
- 混合マテリアルに対応した安全なデフォルト値の自動無音検出
- `_tc_<sample_offset>` ベースのタイムライン復元
- BWF `bext` タイムリファレンス対応
- 一般的な命名規則の変更に対応するトラックマッチング
- `dry-run`、ログ、マニフェスト出力による作業の追跡可能性
- 複数の無音検出モード（ハイブリッド、ピーク、RMS）
- 異なるオーディオマテリアル用の柔軟なトリムパラメータ
- テンポオートメーション対応のサンプル→ビート変換 — BPMが変化するプロジェクトでもクリップ位置を正確に維持

## 要件

- Python 3.10 以上

## インストール

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -e .
```

CLI コマンド:

```bash
ableton-strip-silence
```

## クイックスタート

```bash
ableton-strip-silence auto \
  --als path/to/project.als \
  --exports path/to/live-exports
```

デフォルト出力:

- 作業ディレクトリ: `<als-dir>/ableton-strip-silence/<als-stem>/`
- 復元された ALS: `<als-dir>/ableton-strip-silence/<als-stem>/<als-stem>.strip_silence.als`
- ログファイル: `<work-dir>/auto.log`
- 結果マニフェスト: `<work-dir>/auto_manifest.json`

デフォルトでは、`auto` は一致するトラックの既存 `AudioClip` をクリア します（`--keep-existing` を使用して追加に切り替える）。

## コマンド

すべてのコマンドは、完了時に stdout に JSON 形式の結果オブジェクトを出力します。

### `auto`（推奨）

rename + strip-silence + restore を 1 つの自己完結型パイプラインで実行します。

**エイリアス**: `phase1` と `phase2` は `rename` と `restore` のレガシーエイリアスです。

### `rename` (phase1)

エクスポートされた WAV ファイルを名前変更して、ALS トラック順序とグループ階層を保持します。

```bash
ableton-strip-silence rename \
  --als path/to/project.als \
  --exports path/to/live-exports \
  --output path/to/renamed-exports
```

デフォルトログ: `<output>/phase1.log`

### `strip-silence`

非無音領域を検出し、`_tc_` タイムコード付き WAV クリップを書き込みます。

```bash
ableton-strip-silence strip-silence \
  --inputs path/to/renamed-exports \
  --output path/to/timecoded-clips
```

デフォルトログ: `<output>/strip_silence.log`

### `restore` (phase2)

`_tc_` タイムコード付き WAV クリップを Ableton `.als` アレンジメントに復元します。

```bash
ableton-strip-silence restore \
  --als path/to/base.als \
  --clips path/to/timecoded-clips \
  --output path/to/restored.als
```

デフォルトログ: `<output>.parent/phase2.log`

## グローバルオプション

- `--log-level {DEBUG,INFO,WARNING,ERROR}`: ログレベルを設定します（デフォルト: `INFO`）
- `--log-file path/to/run.log`: ログをファイルに書き込みます（デフォルトはコマンド固有）

## コマンド固有のオプション

### 出力とメタデータ

- `--dry-run`: ファイルの書き込みや ALS の変更を実行せず、操作をプレビューします
- `--manifest path/to/manifest.json`: 実行メタデータを JSON ファイルに保存します

### 無音検出パラメータ

- `--threshold-db VALUE`: dBFS での固定無音閾値（省略すると、ファイルごとの適応的な閾値処理を使用）
- `--min-silence-ms VALUE`（デフォルト: 350）: クリップを分割するための最小無音ギャップ長（ミリ秒）
- `--min-clip-ms VALUE`（デフォルト: 80）: この期間より短い検出クリップを削除します（ミリ秒）
- `--keep-leading-ms VALUE`（デフォルト: 20）: 検出されたアクティブ領域の前に、この分のオーディオを保持します（ミリ秒）
- `--keep-trailing-ms VALUE`（デフォルト: 40）: 検出されたアクティブ領域の後に、この分のオーディオを保持します（ミリ秒）
- `--window-ms VALUE`（デフォルト: 20）: 分析ウィンドウの長さ（ミリ秒）
- `--hop-ms VALUE`（デフォルト: 10）: 分析ホップの長さ（ミリ秒）

### アクティビティ検出

- `--detection {hybrid,peak,rms}`（デフォルト: `hybrid`）
  - `hybrid`: ピークと RMS の組み合わせ検出。混合マテリアルの安全なデフォルト
  - `peak`: ピークベースの検出（より積極的）
  - `rms`: RMS ベースの検出

- `--mode {independent,linked}`（デフォルト: `independent`）
  - `independent`: 各ファイルを独立してトリムします
  - `linked`: すべてのファイルに対して 1 つのリンクされた編集マップを使用します

### ALS 固有のオプション

- `--bpm VALUE`: BPM をオーバーライドします（テンポオートメーションは無効化され、一定BPMで計算されます）

### オーディオクリップ管理（`auto` コマンドのみ）

- `--clear-existing`: 既存の AudioClips を対象トラックから削除してから挿入します（デフォルト動作）
- `--keep-existing`: マッチしたトラックを置き換える代わりに、ストリップしたクリップを追加します

## 終了コード

成功時は `0` を返します。出力は常に stdout に JSON 形式で送られます。

---

## 初めて使う人へ

### ステップバイステップガイド

初めてこのツールを使う場合は、以下の流れで始めてください。

### ステップ 1: テスト実行

まず必ず `--dry-run` オプションで予行演習を行ってください。**ファイルに変更を加えずに、処理内容だけを確認できます**。

```bash
ableton-strip-silence auto \
  --als path/to/project.als \
  --exports path/to/live-exports \
  --dry-run
```

ログ出力を確認して、期待通りの処理になっているか確認してください。

### ステップ 2: 実データでテスト

`project.als` と `live-exports` フォルダをコピーして、コピーしたファイルで試してください。

```bash
ableton-strip-silence auto \
  --als path/to/project_copy.als \
  --exports path/to/live-exports-copy \
  --log-level DEBUG
```

`--log-level DEBUG` で詳細なログを出力することで、何が起きているかを詳しく確認できます。

### ステップ 3: 本番実行

うまくいくことを確認したら、元のファイルで実行します。

```bash
ableton-strip-silence auto \
  --als path/to/project.als \
  --exports path/to/live-exports
```

### ファイルパスの指定方法

パスの指定には以下のルールがあります：

- **絶対パス**: `C:\Users\username\Music\project.als` または `/home/username/Music/project.als`
- **相対パス**: カレントディレクトリからの相対位置。例：`./project.als` や `../exports`
- **スペース含む場合**: ダブルクォートで囲む

```bash
ableton-strip-silence auto \
  --als "C:\Users\username\My Documents\project.als" \
  --exports "C:\Users\username\Live Exports"
```

### ログファイルの確認方法

問題が生じた場合は、ログファイルを確認してください。

```bash
type <work-dir>/auto.log
```

ログは時系列で処理内容が記録されており、問題の原因特定に役立ちます。

---

## よくある落とし穴と注意点

### ⚠️ 注意点 1: `.als` ファイルは安全な場所にバックアップを取る

このツールは `.als` ファイルを直接書き込みます。**万が一失敗した場合、元のファイルが上書きされる可能性があります**。

必ず以下のいずれかを実行してください：

- 元のファイルをコピーして、コピーしたファイルに対して試す
- `--dry-run` で事前に確認する
- バージョン管理ツール（Git など）でファイルを管理する

### ⚠️ 注意点 2: トラック名が正確に一致する必要がある

`rename` コマンドでは、Ableton Live からエクスポートされた WAV ファイル名と、`.als` ファイルのトラック名をマッチングします。

- **完全一致は求められません**が、**できるだけ類似した名前**を使用してください
- トラック名が「Kick」の場合、ファイル名は「Kick」や「Kick 01」なら認識されやすい
- 「K」「KK」といった大幅に異なる名前は認識されない可能性があります

**ログをよく確認して、正しくマッチしているか確認しましょう**。

### ⚠️ 注意点 3: `.als` ファイルのバージョンに注意

Ableton Live のバージョンが大きく異なると、XML 構造が変わることがあります。

- 古いバージョンの Live で保存した `.als` ファイルは、新しいバージョンで予期しない動作になる可能性があります
- Live 12 以降の `.als` ファイル推奨です

### ⚠️ 注意点 4: 無音検出パラメータはオーディオマテリアルに依存する

デフォルト値は**混合マテリアル（ボーカル + ビートなど）向け**に最適化されています。

- **ボーカルのみ**: `--threshold-db -35` など高めの値を試す
- **ビートのみ**: `--detection peak` など積極的な検出を試す
- **ドローン・パッド**: `--min-silence-ms 500` など長い無音を検出する値を試す

各マテリアル種別に応じて調整が必要な場合があります。**小規模なファイルで試してから本番に臨んでください**。

### ⚠️ 注意点 5: `--keep-existing` の動作

通常は `--clear-existing`（デフォルト）で、既存のクリップは削除されます。

```bash
ableton-strip-silence auto \
  --als path/to/project.als \
  --exports path/to/live-exports \
  --keep-existing  # 既存のクリップを保持して追加
```

を使用する場合、既存のクリップと新しいクリップが混在するため、**タイムライン上で重複したり、意図しない配置になる可能性があります**。慎重に使用してください。

### ⚠️ 注意点 6: ディレクトリの大文字・小文字

Windows と Mac/Linux ではファイルシステムの扱いが異なります：

- **Windows**: 大文字小文字を区別しない（`Export` と `export` は同じ）
- **Mac/Linux**: 大文字小文字を区別する（`Export` と `export` は異なる）

クロスプラットフォーム対応を考慮して、**ディレクトリ名は一貫性を持たせましょう**。

---

## トラブルシューティング

### トラックがマッチしない

**原因**: トラック名と WAV ファイル名が大きく異なっている

**対策**:

1. ログファイルを確認（`auto.log`）
2. `rename` コマンドで生成されたファイル名を確認
3. 必要に応じて `.als` ファイルのトラック名を調整
4. `--manifest` オプションでマッチング結果を JSON で確認

```bash
ableton-strip-silence auto \
  --als path/to/project.als \
  --exports path/to/live-exports \
  --manifest result.json
```

### 無音が正しく検出されない

**原因**: デフォルト値がマテリアルに合っていない

**対策**:

1. `--log-level DEBUG` で詳細ログを確認
2. 小規模ファイルで異なるパラメータを試す
3. `--threshold-db VALUE` で閾値を固定値に変更してテスト

```bash
ableton-strip-silence strip-silence \
  --inputs path/to/inputs \
  --output path/to/outputs \
  --threshold-db -30 \
  --dry-run
```

### エラーが発生する

**一般的なエラーメッセージ**:

- `FileNotFoundError`: ファイルパスが間違っている。パスを確認してください
- `XMLParseError`: `.als` ファイルが破損しているか、サポート対象外のバージョンです
- `No matching tracks found`: トラック名とファイル名が合致していません

**対策**: 常に `--log-level DEBUG` と `--dry-run` で詳細を確認してください

---

## 初心者向けのティップス

### ✅ Tip 1: 最初は 1 つのトラックだけ試す

`rename` と `strip-silence` を個別のコマンドで実行してテストすることで、各ステップの動作を理解できます。

```bash
# ステップ 1: rename だけ
ableton-strip-silence rename \
  --als path/to/project.als \
  --exports path/to/live-exports \
  --output ./renamed-exports \
  --dry-run

# ステップ 2: strip-silence だけ
ableton-strip-silence strip-silence \
  --inputs ./renamed-exports \
  --output ./timecoded \
  --dry-run

# ステップ 3: restore だけ
ableton-strip-silence restore \
  --als path/to/project.als \
  --clips ./timecoded \
  --output ./restored.als \
  --dry-run
```

### ✅ Tip 2: マニフェストを活用する

`--manifest` オプションで JSON 形式の詳細な実行結果を保存できます。

```bash
ableton-strip-silence auto \
  --als path/to/project.als \
  --exports path/to/live-exports \
  --manifest result.json
```

`result.json` を確認することで、何がマッチして、何が無視されたかが一目でわかります。

### ✅ Tip 3: 作業ディレクトリを整理する

`auto` コマンドは自動的に作業ディレクトリを作成します。複数プロジェクトを処理する場合は、**プロジェクトごとに `.als` ファイルと `exports` フォルダを別ディレクトリに分けておく**と混乱を避けられます。

```text
projects/
├── project_1/
│   ├── project_1.als
│   └── exports/
└── project_2/
    ├── project_2.als
    └── exports/
```

### ✅ Tip 4: 小さなサンプルで試す

`.als` ファイルを新規作成して、トラック 2～3 個だけで動作確認することをお勧めします。本番プロジェクトで試す前に、最小限の例で理解を深めましょう。

---

## 推奨ワークフロー

初心者向けの推奨実行順序：

```bash
# 1. テストファイルの準備
cp project.als project_test.als
cp -r exports exports_test

# 2. dry-run で確認
ableton-strip-silence auto \
  --als ./project_test.als \
  --exports ./exports_test \
  --dry-run \
  --log-level DEBUG

# 3. ログを確認して問題がないか確認
cat <work-dir>/auto.log

# 4. マニフェストで詳細確認（オプション）
ableton-strip-silence auto \
  --als ./project_test.als \
  --exports ./exports_test \
  --manifest result.json \
  --dry-run

# 5. 実行
ableton-strip-silence auto \
  --als ./project_test.als \
  --exports ./exports_test \
  --manifest result.json

# 6. 結果を確認
type result.json
```

---
