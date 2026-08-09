"""Apify/Zyte 站点模板知识提取 — 从官方 Agent Skills 仓库提取站点→字段映射。

Apify 的 apify-ultimate-scraper 覆盖 130+ 主流平台（Instagram, Facebook, TikTok,
YouTube, X, LinkedIn, Google Maps, Amazon, Airbnb 等）。

此模块将这些平台知识提取为 OmniCrawler 可用的模板参考，
生成 templates/sites/ 下的专用配置文件。

注意: 这些是公开的平台结构知识（非代码），不涉及许可问题。
"""

from __future__ import annotations

from ..core.utils import user_agent

# ── Apify Ultimate Scraper 覆盖的 130+ 平台 ──────────────────────────
# 信息来源: https://github.com/apify/agent-skills (Apache 2.0)

APIFY_ACTOR_MAP: dict[str, dict[str, str]] = {
    # 社交媒体
    "instagram": {
        "category": "social_media",
        "actor": "apify/instagram-scraper",
        "typical_fields": "username,full_name,biography,followers_count,following_count,posts_count,post_caption,post_likes,post_comments,post_timestamp,hashtags,mentions,image_url,video_url",
        "notes": "需登录。支持帖子/评论/reels/标签/用户资料。限速严格 (200 req/h)",
    },
    "facebook": {
        "category": "social_media",
        "actor": "apify/facebook-scraper",
        "typical_fields": "page_name,category,likes,followers,post_text,post_likes,post_shares,post_comments,post_timestamp,image_url,link_url",
        "notes": "需登录。支持页面/帖子/评论/事件",
    },
    "tiktok": {
        "category": "social_media",
        "actor": "apify/tiktok-scraper",
        "typical_fields": "username,nickname,followers,following,video_count,likes,video_description,video_plays,video_likes,video_comments,video_shares,hashtags,music_title,download_url",
        "notes": "支持用户/视频/话题标签/音乐。API 变化频繁",
    },
    "youtube": {
        "category": "social_media",
        "actor": "apify/youtube-scraper",
        "typical_fields": "channel_name,subscribers,video_title,video_views,video_likes,video_comments,video_duration,publish_date,description,thumbnail_url,transcript",
        "notes": "支持频道/视频/评论/字幕/搜索结果",
    },
    "x_twitter": {
        "category": "social_media",
        "actor": "apify/twitter-scraper",
        "typical_fields": "username,display_name,followers,following,tweet_text,tweet_likes,tweet_retweets,tweet_replies,tweet_timestamp,hashtags,mentions,media_urls,quoted_tweet",
        "notes": "需登录。支持用户/推文/搜索/列表。限速严格",
    },
    "linkedin": {
        "category": "social_media",
        "actor": "apify/linkedin-scraper",
        "typical_fields": "name,headline,location,current_company,position,connections,profile_url,company_name,company_size,company_industry,job_title,job_description,job_location,post_text,post_likes,post_comments",
        "notes": "需登录。支持个人/公司/职位/帖子",
    },
    "reddit": {
        "category": "social_media",
        "actor": "apify/reddit-scraper",
        "typical_fields": "subreddit,post_title,post_text,post_score,post_comments_count,author,post_timestamp,post_url,comment_text,comment_score",
        "notes": "支持子版块/帖子/评论/搜索。有公开 API",
    },

    # 搜索引擎 & 地图
    "google_maps": {
        "category": "maps",
        "actor": "apify/google-maps-scraper",
        "typical_fields": "place_name,address,phone,website,rating,reviews_count,category,opening_hours,latitude,longitude,price_level,photos,review_text,review_rating,review_date",
        "notes": "支持搜索/详情/评论。建议使用代理轮换",
    },
    "google_search": {
        "category": "search",
        "actor": "apify/google-search-scraper",
        "typical_fields": "title,url,description,position,site_links,rich_snippet",
        "notes": "支持有机结果/广告/图片/新闻/视频",
    },
    "google_trends": {
        "category": "search",
        "actor": "apify/google-trends-scraper",
        "typical_fields": "keyword,interest_over_time,related_queries,related_topics,geo_breakdown",
        "notes": "支持趋势/相关查询/地理分布",
    },

    # 电商
    "amazon": {
        "category": "ecommerce",
        "actor": "apify/amazon-scraper",
        "typical_fields": "product_title,price,original_price,rating,reviews_count,availability,brand,category,asin,description,bullet_points,image_urls,variant_info,seller_name,bestseller_rank,review_text,review_rating,review_date,review_verified",
        "notes": "支持产品/搜索/评论/畅销榜。需要代理",
    },
    "walmart": {
        "category": "ecommerce",
        "actor": "apify/walmart-scraper",
        "typical_fields": "product_name,price,was_price,rating,reviews_count,brand,upc,description,image_urls,availability,category",
        "notes": "支持搜索/产品/评论",
    },
    "ebay": {
        "category": "ecommerce",
        "actor": "apify/ebay-scraper",
        "typical_fields": "title,price,shipping_price,condition,seller_name,seller_rating,item_location,bids_count,time_left,image_url,item_specifics",
        "notes": "支持搜索/产品/卖家",
    },

    # 旅游 & 房产
    "booking": {
        "category": "travel",
        "actor": "apify/booking-scraper",
        "typical_fields": "hotel_name,address,price_per_night,rating,reviews_count,amenities,room_types,latitude,longitude,images,review_text,review_score,review_date",
        "notes": "支持搜索/详情/评论",
    },
    "tripadvisor": {
        "category": "travel",
        "actor": "apify/tripadvisor-scraper",
        "typical_fields": "restaurant_name,cuisine_type,price_range,rating,reviews_count,address,phone,website,opening_hours,review_text,review_rating,review_date,photos",
        "notes": "支持餐厅/酒店/景点/评论",
    },
    "airbnb": {
        "category": "travel",
        "actor": "apify/airbnb-scraper",
        "typical_fields": "listing_title,price,rating,reviews_count,host_name,room_type,bedrooms,bathrooms,amenities,latitude,longitude,images,description,review_text",
        "notes": "支持搜索/详情/评论/日历价格",
    },
    "yelp": {
        "category": "travel",
        "actor": "apify/yelp-scraper",
        "typical_fields": "business_name,rating,reviews_count,price_level,categories,address,phone,website,opening_hours,photos,review_text,review_rating,review_date",
        "notes": "支持搜索/详情/评论",
    },

    # 专业平台
    "github": {
        "category": "developer",
        "actor": "apify/github-scraper",
        "typical_fields": "repo_name,description,stars,forks,language,topics,license,last_updated,owner,open_issues,contributors,readme,release_version,release_date",
        "notes": "支持仓库/用户/搜索/趋势/发布。有公开 API",
    },
}


# E16：首页域名与 www.{platform}.com 不一致的平台（key 与 APIFY_ACTOR_MAP 一致）
_PLATFORM_HOME_URLS: dict[str, str] = {
    "x_twitter": "https://x.com/",
    "google_maps": "https://www.google.com/maps",
    "google_search": "https://www.google.com/",
    "google_trends": "https://trends.google.com/",
}


def generate_omnicrawl_template(platform: str) -> str:
    """根据 Apify Actor 知识生成 OmniCrawler YAML 模板。"""
    info = APIFY_ACTOR_MAP.get(platform)
    if not info:
        return ""

    fields_str = info["typical_fields"]
    field_list = [f.strip() for f in fields_str.split(",")]

    # E16：部分平台首页域名不是 www.{platform}.com（x_twitter/google_maps 等），
    # 用白名单覆盖，避免生成错误入口
    seed_url = _PLATFORM_HOME_URLS.get(platform, f"https://www.{platform}.com/")

    # 构建字段定义
    field_lines: list[str] = []
    for f in field_list[:12]:  # 最多 12 个字段
        key = f.replace("@", "_").replace(".", "_")
        field_lines.append(f"    {key}:")
        field_lines.append("      selector: \"\"  # 请根据实际页面 CSS 选择器填写")
        field_lines.append(f"      desc: \"{f}\"")

    fields_yaml = "\n".join(field_lines) if field_lines else "    {}"

    template = f"""# Auto-generated from Apify Actor knowledge: {info['actor']}
# Category: {info['category']}
# Notes: {info['notes']}
# Source: https://github.com/apify/agent-skills (Apache 2.0)
#
# ⚠ 此模板仅包含知识参考，选择器需根据实际页面填写。
# 建议配合 omnicrawl auto-analyze 或 visual-select 自动生成选择器。

project:
  name: {platform}_scraper
  workspace: work/{platform}

source:
  kind: browser
  seeds:
    - {seed_url}

crawl:
  max_pages: 100
  max_depth: 2
  same_host: true
  concurrency: 2

http:
  user_agent: "{user_agent("+bot")}"
  respect_robots: true
  delay_seconds: 3.0

browser:
  engine: playwright
  headless: true
  wait_until: networkidle

extract:
  mode: html
  fields:
{fields_yaml}

outputs:
  jsonl: true
  csv: true
  xlsx: true
"""
    return template


def generate_all_templates(output_dir: str) -> int:
    """生成所有已知平台的模板文件。返回生成数量。"""
    from pathlib import Path
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    for platform in APIFY_ACTOR_MAP:
        template = generate_omnicrawl_template(platform)
        if template:
            (target / f"{platform}.yaml").write_text(template, encoding="utf-8")
            count += 1
    return count


# ── CLI ────────────────────────────────────────────────────────────────
def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Apify 站点模板生成器")
    parser.add_argument("--list", action="store_true", help="列出所有已知平台")
    parser.add_argument("--generate", metavar="PLATFORM", help="生成指定平台模板")
    parser.add_argument("--all", metavar="DIR", help="生成所有平台模板到指定目录")
    args = parser.parse_args()

    if args.list:
        for platform, info in sorted(APIFY_ACTOR_MAP.items()):
            print(f"  {platform:20s} [{info['category']:12s}] {info['actor']}")
        return

    if args.generate:
        template = generate_omnicrawl_template(args.generate)
        if template:
            print(template)
        else:
            print(f"未知平台: {args.generate}")
        return

    if args.all:
        count = generate_all_templates(args.all)
        print(f"已生成 {count} 个平台模板到 {args.all}/")


if __name__ == "__main__":
    main()
