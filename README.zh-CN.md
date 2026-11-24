[English](README.md) | 简体中文

# MineMyDinner

**从你自己收藏的菜谱和冰箱里，挖出今晚的晚饭。**

## 为什么做这个

我收藏了很多菜谱——视频、帖子、截图——可总是想不起来做。真到做饭的时候，那些收藏早就被忘在角落里了。

MineMyDinner 把这些收藏变成可以直接问的东西：“今晚两个人，三个菜，只用冰箱里有的”。最关键的取舍是：**它从不凭空编菜**。计划只从你自己导入并确认过的菜谱里选，所以每个建议都是你本来就想做的菜，还能链回你当初收藏的地方。语言模型负责读懂你的要求、在你的菜谱库里挑菜；代码负责对照冰箱库存、厨具、可用时间，以及每个人的过敏和饮食限制来检查结果。

## 能做什么

- **导入菜谱**：粘贴文字、网页链接、截图或批量文本文件。模型提取菜名、食材、用量和厨具，你检查、修改草稿后再保存。每道菜都保留原始来源。
- **记录家里有什么**：冰箱库存、常备调料、厨具。
- **在聊天里安排一顿饭**：用自己的话说想吃什么。可以只用现有食材，也可以允许买菜并看到缺什么。
- **多人吃饭**：确认这顿谁吃，大家的过敏和饮食限制合并到这顿饭里。朋友的信息只有你选择保存时才会保存。
- **调整计划**：可以手动删菜、加菜，也可以直接说“茄子换一个”“清淡点”“今晚不要猪肉”——追问只改你要求的部分，回复会说明改了什么、哪些没能做到。
- **全程中英双语**：界面、菜谱和食材词典都支持中文和英文。

## 怎么构建的

| 部分 | 选择 |
|---|---|
| 后端 | Python、FastAPI、SQLAlchemy、Alembic |
| Agent | LangGraph；DeepSeek、OpenAI、Gemini 通过统一的模型层接入，按任务选择 |
| 数据库 | PostgreSQL + pgvector（业务数据、向量检索、对话检查点） |
| 前端 | Next.js、React、next-intl |

几个贯穿整个项目的设计思路：

- **模型负责理解，硬约束交给代码。** 锁定的菜、菜的数量、过敏、这顿饭的忌口、库存、厨具和时间限制都在代码里检查，不靠提示词。追问修改计划时，模型先读懂这句话想要什么，代码算出哪些菜可以动，模型只在这个范围里挑菜。
- **靠评测说话，不靠感觉。** 菜谱提取和饭菜规划都有评测：开发集、回归集和留作检验的场景集，每一轮都记录在 [docs/planning-evaluation-history.md](docs/planning-evaluation-history.md)。提示词有版本号，一旦被评测用过就不再修改（[docs/prompt-versions.md](docs/prompt-versions.md)）。
- **谨慎对待别人的信息。** 保存的昵称永远不会发给模型；吃饭的人以匿名标签发送。

更多细节：[docs/design.md](docs/design.md)（整体设计），[docs/planner-what-each-model-sees.md](docs/planner-what-each-model-sees.md)（每个模型具体收到什么）。这些文档是英文的。

## 进度

个人项目，正在开发中。第 0 阶段（菜谱导入、审核、菜谱库、库存、吃饭的人）已完成；第 1 阶段（规划 Agent 和聊天）进行中。之后计划做：评分和记忆、食材替换、本地微调的提取模型、视频导入。

## 本地运行

需要 Docker、Python 3.13、Node.js，以及至少一个模型的 API key。

```powershell
copy .env.example .env      # 然后填入你的 API key
docker compose up -d        # Postgres + pgvector，地址 127.0.0.1:15433

cd backend
pip install -e ".[dev]"
python -m alembic upgrade head
python -m minemydinner.seed   # 食材和厨具词典
python -m uvicorn minemydinner.main:app --host 127.0.0.1 --port 8000

# 另开一个终端
cd frontend
npm install
npm run dev                 # http://localhost:3000
```

完整说明（包括测试和评测脚本）见 [docs/local-development.md](docs/local-development.md)（英文）。

## 许可证

本仓库公开仅供查看。它没有开源许可证：保留所有权利，未授权任何人使用、复制、修改或分发其中的代码和文档。
