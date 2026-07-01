# Smart Drill Architecture

## システム全体

Smart Drill は「教材生成」と「学習管理」を分離した構成を採用する。

``` text
ChatGPT
   │
   ▼
SDP (Smart Drill Package)
   │
   ▼
SDP Import
   │
   ├── 問題DB (SQLite)
   ├── メディア(images/audio)
   └── 学習エンジン
            │
     ┌──────┴──────┐
     ▼             ▼
 今日のおすすめ   テストモード
        │
        ▼
     学習画面
        │
        ▼
     学習履歴DB
        │
        ▼
   親ダッシュボード
```

## レイヤー構成

### UI

-   学習画面
-   インポート画面
-   親画面
-   設定画面

### アプリケーション

-   学習エンジン
-   SDPインポート
-   おすすめ問題生成
-   テストモード

### データ

-   問題DB
-   学習履歴
-   設定
-   メディア管理

### AI連携

-   OCR
-   問題生成
-   類題生成
-   ヒント・解説生成

## データモデル

### Question

-   question_id
-   subject
-   grade
-   category
-   question
-   choices
-   answer
-   hints
-   explanation
-   difficulty
-   importance
-   tags
-   media

### LearningHistory

-   question_id
-   solved_at
-   result
-   next_review
-   streak

### Settings

-   忘却曲線設定
-   テスト日
-   出題設定

## AIとの役割分担

  ChatGPT      Smart Drill
  ------------ ----------------
  OCR          学習管理
  教材生成     出題
  類題生成     復習タイミング
  ヒント生成   学習履歴
  解説生成     分析

## 開発方針

-   問題データと学習履歴を分離する
-   SDPを教材交換フォーマットとする
-   SQLiteを標準DBとする
-   GitHubで設計書とコードを一元管理する
-   Codexを実装支援に利用する
