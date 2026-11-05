"""Turn YouTube Analytics API rows into transparent, actionable content insights."""
from statistics import median


def rows_as_dicts(rows, headers):
    names = [str(h.get("name") or h.get("column") or "") for h in (headers or [])]
    return [dict(zip(names, row)) for row in (rows or [])]


def summarize_video_performance(rows):
    """Summarize top-video metrics without inventing retention or causal explanations."""
    normalized = []
    for row in rows or []:
        if isinstance(row, dict):
            item = dict(row)
        else:
            item = dict(row)
        try:
            views = int(item.get("views", 0) or 0)
            likes = int(item.get("likes", 0) or 0)
            comments = int(item.get("comments", 0) or 0)
            shares = int(item.get("shares", 0) or 0)
            avg_seconds = float(item.get("averageViewDuration", 0) or 0)
            watch_minutes = float(item.get("estimatedMinutesWatched", 0) or 0)
        except (TypeError, ValueError):
            continue
        item.update({
            "views": views, "likes": likes, "comments": comments, "shares": shares,
            "average_view_duration_seconds": avg_seconds,
            "engagement_per_view_pct": ((likes + comments + shares) / views * 100) if views else 0.0,
            "estimated_watch_minutes": watch_minutes,
        })
        normalized.append(item)
    if not normalized:
        return {"videos": [], "insights": ["No video-level analytics were returned for this date range."]}
    view_values = [x["views"] for x in normalized]
    engagement_values = [x["engagement_per_view_pct"] for x in normalized if x["views"]]
    avg_duration_values = [x["average_view_duration_seconds"] for x in normalized if x["average_view_duration_seconds"] > 0]
    view_mid = median(view_values)
    engagement_mid = median(engagement_values) if engagement_values else 0
    duration_mid = median(avg_duration_values) if avg_duration_values else 0
    insights = [
        f"Top video has {max(view_values):,} views in the selected reporting period."
    ]
    if len(normalized) >= 2:
        high_views = max(normalized, key=lambda x: x["views"])
        high_engagement = max(normalized, key=lambda x: x["engagement_per_view_pct"])
        insights.append(
            f"Highest-view video: {high_views.get('title') or high_views.get('video') or 'Video'} "
            f"({high_views['views']:,} views). Inspect its topic, opening and packaging for repeatable patterns."
        )
        insights.append(
            f"Highest engagement-per-view video: {high_engagement.get('title') or high_engagement.get('video') or 'Video'} "
            f"({high_engagement['engagement_per_view_pct']:.2f}%). Compare its audience promise and call to action with other uploads."
        )
    if duration_mid:
        insights.append(f"Median average view duration across returned videos is {duration_mid:.1f} seconds. Compare videos of similar length and format.")
    if engagement_mid:
        insights.append(f"Median likes + comments + shares per view is {engagement_mid:.2f}%. This is a descriptive comparison, not a universal benchmark.")
    for item in normalized:
        item["views_vs_median"] = (item["views"] / view_mid) if view_mid else 0
        item["engagement_vs_median"] = (item["engagement_per_view_pct"] / engagement_mid) if engagement_mid else 0
        item["duration_vs_median"] = (item["average_view_duration_seconds"] / duration_mid) if duration_mid else 0
    return {"videos": sorted(normalized, key=lambda x: x["views"], reverse=True), "insights": insights,
            "median_views": view_mid, "median_engagement_pct": engagement_mid,
            "median_average_view_duration_seconds": duration_mid}


def build_content_recommendations(summary):
    """Offer cautious next experiments from observed metrics; avoid causal claims."""
    videos = summary.get("videos", [])
    if not videos:
        return ["Collect more video-level data before drawing conclusions."]
    recommendations = []
    views = sorted(videos, key=lambda x: x["views"], reverse=True)
    engagement = sorted(videos, key=lambda x: x["engagement_per_view_pct"], reverse=True)
    durations = [x for x in videos if x["average_view_duration_seconds"] > 0]
    if views:
        recommendations.append(
            f"Study the topic and title structure of '{views[0].get('title') or views[0].get('video') or 'the highest-view video'}'; test one related topic without copying its exact packaging."
        )
    if engagement and engagement[0]["engagement_per_view_pct"] > 0:
        recommendations.append(
            f"Review the audience prompt and CTA in '{engagement[0].get('title') or engagement[0].get('video') or 'the highest-engagement video'}'; test a similarly specific question in a future upload."
        )
    if len(durations) >= 2:
        low = min(durations, key=lambda x: x["average_view_duration_seconds"])
        recommendations.append(
            f"Review the opening, pacing and section transitions of '{low.get('title') or low.get('video') or 'the lowest-average-duration video'}'. Average view duration alone does not identify where viewers leave."
        )
    recommendations.append("Run one controlled experiment at a time (topic, title/thumbnail, or opening hook) and compare similar formats over comparable date windows.")
    return recommendations
