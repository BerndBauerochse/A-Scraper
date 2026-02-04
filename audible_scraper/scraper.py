import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse
from typing import List, Optional
from .models import Entry

class AudibleScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        })

    def _normalize_url(self, url: str) -> str:
        """
        Cleans the URL by keeping only specific query parameters:
        searchProvider, sort, page.
        """
        if not url:
            return url
            
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        
        allowed_params = [
            "searchProvider", "sort", "page", 
            "publisher", "feature_seven_browse-bin", 
            "keywords", "narrator", "author_author"
        ]
        new_params = {}
        
        for key in allowed_params:
            if key in query_params:
                new_params[key] = query_params[key]
        
        new_query = urlencode(new_params, doseq=True)
        
        new_url = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment
        ))
        return new_url

    def fetch_all_pages(self, start_url: str, progress_callback=None) -> List[Entry]:
        """Fetches all pages starting from the given URL."""
        all_entries = []
        # Normalize the start URL
        current_url = self._normalize_url(start_url)
        page_num = 1
        
        # Keep track of visited URLs to avoid cycles even with normalization
        visited_urls = set()
        
        while current_url:
            if current_url in visited_urls:
                print(f"Cycle detected or duplicate page: {current_url}")
                break
            visited_urls.add(current_url)
            
            print(f"Fetching: {current_url}")
            if progress_callback:
                progress_callback(page_num)
                
            try:
                response = self.session.get(current_url, timeout=10)
                response.raise_for_status()
                
                entries, next_page_url = self.parse_page(response.text, current_url)
                
                if page_num == 1:
                    with open("debug_audible.html", "w", encoding="utf-8") as f:
                        f.write(response.text)
                        
                all_entries.extend(entries)
                
                if next_page_url:
                    current_url = self._normalize_url(next_page_url)
                else:
                    current_url = None
                    
                page_num += 1
                
            except requests.RequestException as e:
                print(f"Error fetching {current_url}: {e}")
                break
                
        return all_entries

    def parse_page(self, html: str, base_url: str) -> (List[Entry], Optional[str]):
        """Parses a single page HTML and returns entries and the next page URL."""
        soup = BeautifulSoup(html, "html.parser")
        entries = []
        
        # Find all product list items
        # Audible structure usually has li elements with class 'productListItem'
        # or similar containers. We need to be robust.
        # Inspecting typical Audible search results:
        # <li class="bc-list-item productListItem" ...>
        
        product_items = soup.find_all("li", class_="productListItem")
        
        if not product_items:
            # Fallback or check if structure is different
            # Sometimes it's just rows in a table or different list classes
            pass

        for item in product_items:
            entry = self._extract_entry(item)
            if entry:
                entries.append(entry)
        
        # Find next page link
        # Usually <span class="nextButton"><a href="...">Next</a></span>
        next_link = soup.find("span", class_="nextButton")
        next_url = None
        if next_link:
            a_tag = next_link.find("a")
            if a_tag and "href" in a_tag.attrs:
                next_url = urljoin(base_url, a_tag["href"])
        
        return entries, next_url

    def _extract_entry(self, item_soup) -> Optional[Entry]:
        try:
            # Title and Link
            # Usually in <h3 class="bc-heading ..."> <a href="...">Title</a> </h3>
            title_tag = item_soup.find("h3", class_="bc-heading")
            if not title_tag:
                return None
            
            link_tag = title_tag.find("a")
            if not link_tag:
                return None
            
            raw_title = link_tag.get_text(strip=True)
            raw_url = link_tag["href"]
            
            # Clean URL
            # https://www.audible.de/pd/Title/B0XYZ...
            full_url = urljoin("https://www.audible.de", raw_url)
            clean_url = full_url.split("?", 1)[0]
            
            # Extract ID (ASIN)
            # Usually the last part of the path or second to last
            # /pd/Title-Audiobook/B0XYZ...
            # We can use regex to find B0...
            asin_match = re.search(r"(B[0-9A-Z]{9})", clean_url)
            if asin_match:
                entry_id = asin_match.group(1)
            else:
                # Fallback: use the whole clean URL as ID if ASIN not found
                entry_id = clean_url

            # Release Date
            # Usually: <li class="bc-list-item releaseDateLabel"> ... <span> 20.08.2021 </span> ... </li>
            release_date = ""
            date_li = item_soup.find("li", class_="releaseDateLabel")
            if date_li:
                # The date is usually in a span inside or just text
                # Format: "Erscheinungsdatum: 20.08.2021"
                date_text = date_li.get_text(strip=True)
                # Extract date pattern DD.MM.YYYY
                date_match = re.search(r"(\d{2}\.\d{2}\.\d{4})", date_text)
                if date_match:
                    release_date = date_match.group(1)

            # Price
            # "Preis ohne Abo"
            # Look for text "Preis ohne Abo" and get the price nearby
            price_text = ""
            
            # Strategy 1: Look for "Preis ohne Abo" text and find price in siblings/parent
            buybox_div = item_soup.find("div", class_="adbl-buybox-area")
            if buybox_div:
                 all_text = buybox_div.get_text(" ", strip=True)
                 # Pattern: "Preis ohne Abo: 18,95 €" or similar
                 # User report: "22,95oder kostenlos..."
                 # We look for a price pattern that might be followed by "oder" or "€"
                 # Regex: (\d+,\d+)\s?(?:€|Euro)?
                 
                 # First try with explicit label "Preis ohne Abo"
                 price_match = re.search(r"Preis ohne Abo.*?(\d+,\d+\s?€?)", all_text, re.IGNORECASE)
                 
                 if not price_match:
                     # Fallback 1: Look for price followed by "oder kostenlos" (User reported pattern)
                     # Example: "22,95oder kostenlos"
                     # We use a regex that finds a price, then maybe some garbage/space, then "oder kostenlos"
                     # \d+,\d+ matches "22,95"
                     # .*? matches the weird char
                     # oder kostenlos matches the text
                     fallback_match = re.search(r"(\d+,\d+).*?oder kostenlos", all_text, re.IGNORECASE)
                     if fallback_match:
                         price_text = fallback_match.group(1) + " €"
                     else:
                         # Fallback 2: Just take the first price found in the buybox
                         price_matches = re.findall(r"(\d+,\d+)\s?(?:€|Euro|EUR)?", all_text)
                         if price_matches:
                             price_text = price_matches[0] + " €"
                 else:
                     price_text = price_match.group(1)
            
            # Strategy 2: User reported container "bc-row bc-spacing-top-none"
            if not price_text:
                # Try to find this specific container
                # Note: class search in BS4 with string "a b" looks for exact match of class string, 
                # but classes are usually a list.
                # We use select_one for CSS selector which is easier for multiple classes
                other_container = item_soup.select_one("div.bc-row.bc-spacing-top-none")
                if other_container:
                     all_text = other_container.get_text(" ", strip=True)
                     # Look for price pattern
                     price_matches = re.findall(r"(\d+,\d+)\s?(?:€|Euro|EUR)?", all_text)
                     if price_matches:
                         price_text = price_matches[0] + " €"

            # Runtime
            # <li class="bc-list-item runtimeLabel"> ... Spieldauer: 10 Std. und 3 Min. ... </li>
            runtime_minutes = 0
            runtime_li = item_soup.find("li", class_="runtimeLabel")
            if runtime_li:
                runtime_text = runtime_li.get_text(strip=True)
                # Patterns: 
                # "Spieldauer: 10 Std. und 3 Min."
                # "Spieldauer: 5 Min."
                # "Spieldauer: 1 Std."
                
                hours = 0
                minutes = 0
                
                # Extract hours
                hours_match = re.search(r"(\d+)\s*Std\.", runtime_text)
                if hours_match:
                    hours = int(hours_match.group(1))
                
                # Extract minutes
                minutes_match = re.search(r"(\d+)\s*Min\.", runtime_text)
                if minutes_match:
                    minutes = int(minutes_match.group(1))
                    
                runtime_minutes = hours * 60 + minutes

            # Subtitle
            # <li class="bc-list-item subtitleLabel"> ... </li>
            # <h2 slot="subtitle">...</h2>
            subtitle = ""
            
            # Strategy 1: h2 with slot="subtitle" (User provided)
            subtitle_tag = item_soup.find("h2", attrs={"slot": "subtitle"})
            if subtitle_tag:
                subtitle = subtitle_tag.get_text(strip=True)
            
            # Strategy 2: li with class subtitleLabel
            if not subtitle:
                subtitle_li = item_soup.find("li", class_="subtitleLabel")
                if subtitle_li:
                    subtitle = subtitle_li.get_text(strip=True)

            # Strategy 3: li with class subtitle (Found in debug HTML)
            if not subtitle:
                subtitle_li = item_soup.find("li", class_="subtitle")
                if subtitle_li:
                    subtitle = subtitle_li.get_text(strip=True)
            
            # Strategy 4: Fallback "Untertitel:"
            if not subtitle:
                # Fallback: Look for any li containing "Untertitel:"
                for li in item_soup.find_all("li", class_="bc-list-item"):
                    text = li.get_text(strip=True)
                    if text.startswith("Untertitel:"):
                         subtitle = text
                         break
            
            # Clean up "Untertitel:" prefix if present
            if subtitle.startswith("Untertitel:"):
                subtitle = subtitle.replace("Untertitel:", "", 1).strip()

            # Author
            # <li class="bc-list-item authorLabel"> ... </li>
            author = ""
            author_li = item_soup.find("li", class_="authorLabel")
            if author_li:
                author = author_li.get_text(strip=True)
                # Remove "Autor:" prefix
                if author.startswith("Autor:"):
                    author = author.replace("Autor:", "", 1).strip()
                elif author.startswith("Von:"):
                    author = author.replace("Von:", "", 1).strip()

            # Rating
            # <li class="bc-list-item ratingsLabel"> ... <span class="bc-text bc-pub-offscreen"> 4.5 von 5 Sternen </span> ... 94 Bewertungen ... </li>
            rating = ""
            rating_count = 0
            ratings_li = item_soup.find("li", class_="ratingsLabel")
            if ratings_li:
                rating_text = ""
                # Try to find the hidden text which is usually cleaner "X von 5 Sternen"
                rating_span = ratings_li.find("span", class_="bc-pub-offscreen")
                if rating_span:
                    rating_text = rating_span.get_text(strip=True)
                else:
                    # Fallback to whatever text is there
                    rating_text = ratings_li.get_text(strip=True)
                
                # Extract number: "4,5" or "5"
                match = re.search(r"(\d+(?:[.,]\d+)?)", rating_text)
                if match:
                    val_str = match.group(1).replace(",", ".")
                    try:
                        val = float(val_str)
                        # Format to 1 decimal place, use comma
                        rating = "{:.1f}".format(val).replace(".", ",")
                    except ValueError:
                        rating = rating_text
                else:
                    rating = rating_text
                
                # Extract Count: "94 Bewertungen" or "1.234 Bewertungen"
                # We search in the full text of the li
                # Use " " separator to avoid merging text like "Sternen123"
                full_rating_text = ratings_li.get_text(" ", strip=True)
                
                # Find all matches because sometimes "Bewertung" might appear in the rating part (rare but possible)
                # or text merging caused issues. We take the last match as the count is usually at the end.
                # Use negative lookbehind (?<!,) to avoid matching the decimal part of a rating (e.g. "4,5" merged with "123" -> "5123")
                count_matches = re.findall(r"(?<!,)([\d.]+)\s*Bewertung", full_rating_text)
                
                if count_matches:
                    # Take the last match
                    count_str = count_matches[-1].replace(".", "") # Remove thousands separator
                    try:
                        rating_count = int(count_str)
                    except ValueError:
                        pass

            return Entry(
                id=entry_id,
                title=raw_title,
                subtitle=subtitle,
                author=author,
                rating=rating,
                rating_count=rating_count,
                url=clean_url,
                price_without_sub=price_text,
                release_date=release_date,
                runtime=runtime_minutes
            )

        except Exception as e:
            print(f"Error extracting entry: {e}")
            return None
