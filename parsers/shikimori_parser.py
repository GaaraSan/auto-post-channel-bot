import requests

HEADERS = {
    "User-Agent": "anime-db/1.0",
    "Accept": "application/json",
}

API_URL = "https://shikimori.one/api/animes/{}"


def parse_shikimori_anime(anime_id: int) -> dict:
    url = API_URL.format(anime_id)

    response = requests.get(url, headers=HEADERS, timeout=10)
    response.raise_for_status()

    data = response.json()

    stats = data.get("rates_statuses_stats") or []

    return {
        "shikimori_id": data["id"],
        "name": data["name"],
        "russian": data["russian"],
        "japanese": data.get("japanese"),
        "description": data["description"],
        "status": data["status"],
        "kind": data.get("kind"),
        "episodes": data["episodes"],
        "episodes_aired": data["episodes_aired"],
        "year": data["aired_on"][:4] if data["aired_on"] else None,
        "rating": data["score"],
        "members": sum(e.get("value", 0) for e in stats) if stats else None,
        "image": f"https://shikimori.one{data['image']['original']}" if data.get("image") else None,
        "genres": [g["name"] for g in data["genres"]],
    }

def get_anime_ids(page: int = 1, limit: int = 50) -> list[int]:
    url = "https://shikimori.one/api/animes"

    params = {
        "page": page,
        "limit": limit,
        "order": "id",
    }

    response = requests.get(url, headers=HEADERS, params=params, timeout=10)
    response.raise_for_status()

    data = response.json()
    return [anime["id"] for anime in data]
