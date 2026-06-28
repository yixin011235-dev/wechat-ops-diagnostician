# 数据契约（canonical schema）

`scripts/parse_exports.py` 把微信导出统一输出下面这个结构。诊断逻辑只认这个结构，不直接碰原始 .xls。

```
{
  "content": {                              # 来自"内容/阅读趋势"导出（OLE2）
    "daily_by_channel":  [{date, channel, readers}],
    "daily_engagement":  [{date, shares, read_origin_clicks, saves, articles_published}],
    "per_article":       [{channel, pub_date, title, readers, read_share}]
  },
  "users": {                                # 来自"常读用户分布"导出（伪 HTML）
    "regular_count": [{month, count, ratio_pct}],   # 常读用户数 + 占比
    "city_tier":     [{month, bucket, count, pct}], # 一线/二线/三线/四线及以下/未知
    "age":           [{month, bucket, count, pct}], # 小于18/18-25/26-35/36-45/46-60/大于60
    "gender":        [{month, bucket, count, pct}]
  },
  "leads": [ ... ],                         # 企微来源线索（埋点上线后，单独喂入，见下）
  "meta": {files, content_date_ranges}
}
```

## 字段口径与已知坑

- **channel（来源）取值**：公众号消息 / 朋友圈 / 搜一搜 / 聊天会话 / 公众号主页 / 推荐 / 其他 / 全部。"全部"是该日总数，算来源结构时要排除。"推荐"即旧"看一看"。
- **per_article 行数 > 发文数**：单篇块列出的是"窗口内拿到阅读的所有文章",含窗口前发布、仍在靠搜索拉新的老文。`articles_published` 之和才是窗口内新发数。两者之差＝长尾老文，本身是一个诊断信号。
- **常读 ≠ 全部受众**：`users.*` 只描述常读用户（占粉丝约 14–16%），不含搜一搜来的陌生读者。对搜索占比高的号，常读画像严重低估真实触达盘。诊断时永远标注这一点。
- **城市只到"层级"**：拿不到省份/城市名。"本地"是推断，不是确证。
- **反推总粉丝**：`总粉丝 ≈ 常读用户数 / 常读比例`。这是免费导出里唯一能间接得到粉丝量的途径。

## 企微线索（leads）—— 埋点上线后单独喂入

公众号导出里**没有任何转化数据**。转化真相来自企微，靠**来源键**和上面的内容数据 join。来源键规范见 `references/source_key_spec.md`。线索结构：

```
[{source_key, lead_city, stage, first_touch, owner}]
# source_key 拆三段 line_asset_placement，asset≠g join 单篇、=g join 条线聚合
# stage: L1加企微 / L2发起咨询 / L3留资 / L4委托
```

没有这层时，诊断照常跑，但"转化"一节明确标注为盲区，并把"建埋点"列为高优先级动作。
