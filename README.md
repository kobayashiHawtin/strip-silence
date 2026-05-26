# ableton-strip-silence

Ableton Live の `.als` と Live 書き出し WAV 群だけを使って、無音カット済みクリップをタイムラインへ自動復元する Python CLI です。

Reaper は不要です。処理は次の3段階で完結します。

1. `.als` からトラック順・グループ階層を解析し、Live 書き出し WAV を連番リネーム
2. WAV の無音を検出し、非無音区間を `_tc_<開始サンプル>` 付き WAV クリップとして書き出し
3. `_tc_` 情報を Beat に変換し、`.als` の該当トラックへ `AudioClip` として再配置

## できること

- Ableton Live のトラック順・グループ階層に合わせた WAV リネーム
- しきい値・最小無音長・前後余白を指定できる無音カット
- `_tc_` ファイル名によるタイムライン位置保持
- BWF `bext` TimeReference の読み取り
- IEEE float WAV（format 3）のメタデータ読み取り
- 既存 `AudioClip` テンプレートを利用した `.als` 復元
- 詳細ログと manifest 出力
- ドライラン

## セットアップ

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -e .
```

CLI 名は `ableton-strip-silence` です。

## 一発実行

通常は `.als` と Live 書き出し WAV フォルダだけを指定します。成果物は Ableton プロジェクトディレクトリ内の `ableton-strip-silence/<als名>/` にまとまって生成されます。

```bash
ableton-strip-silence auto \
  --als path/to/project.als \
  --exports path/to/live-exports
```

生成先を明示したい場合だけ、`--work-dir` と `--output` を指定してください。

```bash
ableton-strip-silence auto \
  --als path/to/project.als \
  --exports path/to/live-exports \
  --work-dir path/to/project/ableton-strip-silence/project \
  --output path/to/project/ableton-strip-silence/project/project.strip_silence.als
```

デフォルトは、トラックごとに安全寄りの自動しきい値を推定する設定です。素材別プリセットを選ぶ必要はありません。

`auto` は、マッチしたトラックについて既存の `AudioClip` を削除してから無音カット済みクリップを配置します。未マッチのトラックは触りません。既存クリップを残して追加配置したい場合だけ `--keep-existing` を指定してください。

## 段階実行

### 1. Live 書き出し WAV のリネーム

```bash
ableton-strip-silence rename \
  --als path/to/project.als \
  --exports path/to/live-exports \
  --output path/to/renamed-exports
```

### 2. 無音カット

```bash
ableton-strip-silence strip-silence \
  --inputs path/to/renamed-exports \
  --output path/to/timecoded-clips
```

### 3. `.als` へ復元

```bash
ableton-strip-silence restore \
  --als path/to/base.als \
  --clips path/to/timecoded-clips \
  --output path/to/restored.als
```

## 無音カット設定

基本的には未指定のデフォルトを推奨します。全トラックに異なる素材が混在していても、各WAVごとに控えめな自動しきい値を推定します。

- `--threshold-db`: 固定しきい値。未指定なら自動推定。低くするほど小さい音も残ります。
- `--min-silence-ms`: 分割対象にする最小無音長。デフォルトは `350ms` で、短い隙間を切りすぎない安全寄りです。
- `--min-clip-ms`: これ未満の短い検出区間を破棄。デフォルトは `80ms`。
- `--keep-leading-ms`: 検出区間の前に残す余白。デフォルトは `20ms`。
- `--keep-trailing-ms`: 検出区間の後に残す余白。デフォルトは `120ms`。
- `--window-ms`: 解析窓幅。デフォルトは `20ms`。
- `--hop-ms`: 解析ステップ。デフォルトは `10ms`。
- `--detection hybrid|peak|rms`: デフォルトは `hybrid`。ピークとRMSの両方を見て、トランジェントと持続音のどちらにも寄せすぎない安全な判定をします。
- `--mode independent|linked`:
  - `independent`: 各トラックを個別に無音カット。デフォルトです。
  - `linked`: 全トラックの検出結果を統合し、同じ編集マップで全トラックを切る。マルチマイクや位相維持が必要な素材向けです。

manifest の `analysis` には、各ファイルで実際に使われた自動しきい値 `threshold_db` が記録されます。

## ログと manifest

- `auto` のデフォルト生成先: `<als-dir>/ableton-strip-silence/<als-stem>/`
- `auto` のデフォルト復元 `.als`: `<als-dir>/ableton-strip-silence/<als-stem>/<als-stem>.strip_silence.als`
- `auto` のデフォルト復元動作: マッチしたトラックの既存 `AudioClip` を置き換え
- `--keep-existing`: `auto` で既存クリップを残して分割クリップを追加
- `--dry-run`: 実ファイル更新なしで何が起きるか確認
- `--log-level DEBUG`: 詳細ログを表示
- `--log-file path/to/run.log`: ログ出力先を明示
- `--manifest path/to/manifest.json`: マニフェスト出力先を明示
- `--bpm 120`: `.als` から BPM を取り出せないときに明示指定

## 注意点

- Ableton Live の `.als` XML 構造はバージョン差異があります。
- Phase restore は既存 `AudioClip` をテンプレートとして再利用できる場合に最も堅牢です。
- `_tc_<開始サンプル>` 付きファイル名が最も安定した復元方法です。
- 実プロジェクト由来の表記揺れ（先頭連番、`(Bounce)`、`[YYYY-MM-DD hhmmss]`）を吸収するマッチングを実装しています。

## セキュリティと公開リポ運用

- `SECURITY.md` に脆弱性報告ポリシーを定義しています。
- `.github/workflows/security.yml` で以下を自動実行します。
  - `pip-audit`（依存脆弱性）
  - `bandit`（Python静的セキュリティ解析）
  - `gitleaks`（シークレット漏えい検出）
- `.github/dependabot.yml` で Actions / pip 依存更新を週次で自動化しています。
- `.gitignore` で `.venv/`、`*.egg-info/`、`out/` などローカル成果物を除外しています。

公開前チェック推奨:

1. `git status` で `.venv/` / `out/` / ローカル Ableton プロジェクトファイルが含まれていないことを確認
2. ローカル絶対パスや秘密情報（APIキー等）がコード・ドキュメントにないことを確認
3. PR 上で Security Checks ワークフローが green であることを確認

ローカルでの事前チェック運用（推奨）:

1. `pre-commit` を導入して `.pre-commit-config.yaml` のフックを有効化
2. コミット前にシークレット漏えい検査（gitleaks）を実行
3. 変更前後で `python -m unittest discover -s tests -v` を実行
4. 必要に応じて `bandit` / `pip-audit` をローカルで再実行

詳細は `CONTRIBUTING.md` を参照してください。
