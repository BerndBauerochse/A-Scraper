from dataclasses import dataclass
from typing import Optional

@dataclass
class Entry:
    id: str
    title: str
    url: str
    price_without_sub: str
    subtitle: str = ""
    author: str = ""
    rating: str = ""
    rating_count: int = 0
    release_date: str = ""
    runtime: int = 0  # Runtime in minutes
    ean: str = ""
    price_digital_de: str = "" # Price from external list
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    is_new: bool = False
    is_changed: bool = False
    changed_fields: list = None

    def __post_init__(self):
        if self.changed_fields is None:
            self.changed_fields = []

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "subtitle": self.subtitle,
            "author": self.author,
            "rating": self.rating,
            "rating_count": self.rating_count,
            "url": self.url,
            "price_without_sub": self.price_without_sub,
            "release_date": self.release_date,
            "runtime": self.runtime,
            "ean": self.ean,
            "price_digital_de": self.price_digital_de,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "is_new": self.is_new,
            "is_changed": self.is_changed
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            id=data["id"],
            title=data["title"],
            subtitle=data.get("subtitle", ""),
            author=data.get("author", ""),
            rating=data.get("rating", ""),
            rating_count=data.get("rating_count", 0),
            url=data["url"],
            price_without_sub=data.get("price_without_sub", ""),
            release_date=data.get("release_date", ""),
            runtime=data.get("runtime", 0),
            ean=data.get("ean", ""),
            price_digital_de=data.get("price_digital_de", ""),
            first_seen=data.get("first_seen"),
            last_seen=data.get("last_seen"),
            is_new=False,
            is_changed=False
        )

    @property
    def runtime_price(self) -> str:
        """Calculates price strictly based on runtime minutes."""
        m = self.runtime
        if m < 60:
            return "" # Or handle as needed, table starts at 60
        if m < 120:
            return "14,95 €"
        if m < 240:
            return "16,95 €"
        if m < 360:
            return "18,95 €"
        if m < 480:
            return "21,95 €"
        if m < 660:
            return "24,95 €"
        if m < 900:
            return "29,95 €"
        if m < 1320:
            return "34,95 €"
        if m < 1680:
            return "39,95 €"
        if m < 2100:
            return "45,95 €"
        if m < 2400:
            return "49,95 €"
        if m < 2700:
            return "55,95 €"
        return "59,95 €"

    @property
    def calculated_price(self) -> str:
        """Returns imported price if available, otherwise runtime price."""
        if self.price_digital_de:
            return self.price_digital_de
        return self.runtime_price
