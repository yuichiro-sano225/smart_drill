# Smart Drill

中学生向け英語4択ドリルWebアプリです。

## 起動方法（Windows）

コマンドプロンプトで以下を実行します。

```cmd
cd C:\study_app\smart_drill
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

ブラウザで開きます。

```text
http://127.0.0.1:5000
```

## 入っている機能

- 子供を選んで英語4択ドリル
- 学年・単元選択
- 学習時間の記録
- 正答率の記録
- 親ダッシュボード
- 単元ごとの正答率
- 連続学習日数
- レベル表示
- バッジ
- 間違えた問題の復習モード

## 問題の追加

`data/questions.csv` を編集すると問題を増やせます。
