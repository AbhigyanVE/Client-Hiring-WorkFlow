import html
import json
import math
import os
import webbrowser
from getpass import getpass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

BENCH_API_URL = "https://d4u8k6edt3njs.cloudfront.net/api/public-ppc/"
BENCH_API_KEY = "cs-pub-5ddf69bc927a254efab53d95c2481c55"


class NoRedirects(HTTPRedirectHandler):
    # Never forward the API key to a redirect destination.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_profiles(api_url, api_key):
    endpoint = urlsplit(api_url)
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or endpoint.username is not None
        or endpoint.password is not None
        or endpoint.fragment
    ):
        raise ValueError("Enter the plain HTTPS URL for the Bench Pool API.")

    query = [
        (name, value)
        for name, value in parse_qsl(endpoint.query, keep_blank_values=True)
        if name not in {"limit", "offset"}
    ]
    client = build_opener(NoRedirects())
    profiles = {}
    offset = 0
    total = None

    for _ in range(200):
        page_limit = 200 if total is None else min(200, total - offset)
        params = urlencode(query + [("limit", page_limit), ("offset", offset)])
        url = endpoint._replace(query=params).geturl()
        request = Request(url, headers={
            "x-api-key": api_key,
            "Accept": "application/json",
        })

        with client.open(request, timeout=20) as response:
            payload = json.load(response)

        if not isinstance(payload, dict) or payload.get("success") is False:
            raise ValueError("The bench API did not return a successful response.")

        rows = payload.get("data")
        page = payload.get("pagination")
        if not isinstance(rows, list) or not isinstance(page, dict):
            raise ValueError("Expected a data list and pagination metadata.")

        try:
            reported_offset = int(page["offset"])
            reported_total = int(page["totalRecords"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("The API did not return valid pagination metadata.") from None

        if reported_offset != offset:
            raise ValueError(f"The API did not honor offset={offset}.")
        if total is not None and reported_total != total:
            raise ValueError("The pool size changed during the fetch. Run the script again.")

        total = reported_total
        print(f"API page: offset={offset}, requested={page_limit}, "
              f"returned={len(rows)}, reported total={total}")

        if not rows and offset < total:
            warning = (
                f"Incomplete directory: the API reported {total} profiles "
                f"but returned an empty page at offset {offset}."
            )
            return list(profiles.values()), warning

        for person in rows:
            if not isinstance(person, dict):
                raise ValueError("The API returned an invalid profile.")
            identifier = person.get("profile_id") or person.get("employee_id")
            if not identifier:
                raise ValueError("The API returned a profile with no ID.")
            identifier = str(identifier)
            if identifier in profiles:
                raise ValueError("A profile appeared twice across pages. Run the script again.")
            profiles[identifier] = person

        # Advance by the actual page size, not the requested page size.
        offset += len(rows)
        print(f"Fetched {offset} / {total} profiles")
        if offset >= total:
            warning = ""
            if offset > total:
                warning = (
                    f"The API returned {offset} distinct profiles but reported "
                    f"{total}. No returned profiles were discarded."
                )
            return list(profiles.values()), warning

    raise ValueError("Reached the page limit before fetching the entire pool.")


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def escape(value):
    return html.escape(str(value), quote=True)


def skill_values(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from skill_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from skill_values(child)
    elif isinstance(value, str) and value.strip():
        yield value.strip()


def photo_url(person):
    images = person.get("images")
    images = [item for item in images if isinstance(item, dict)] if isinstance(images, list) else []
    images.sort(key=lambda item: not item.get("primary"))

    candidates = [person.get("image")]
    for item in images:
        candidates.extend([item.get("thumbnail"), item.get("url")])

    for url in candidates:
        if not isinstance(url, str):
            continue
        try:
            parts = urlsplit(url)
            if (
                parts.scheme == "https"
                and parts.hostname == "nmprofiles.s3.amazonaws.com"
                and parts.username is None
                and parts.password is None
            ):
                return url
        except ValueError:
            continue
    return ""


def render_card(person):
    name = str(person.get("name") or "Name not available").strip()
    role = str(person.get("designation") or "Designation not available")
    category = str(person.get("skill_category_name") or "Category not available")
    availability = str(person.get("availability_status") or "Status not reported")
    bio = str(person.get("job_profile") or "").strip()
    years = number(person.get("total_experience_years"))
    hours = number(person["bench_hours"])

    unique = {}
    for skill in skill_values(person.get("skills_json")):
        unique.setdefault(skill.casefold(), skill)
    skills = sorted(unique.values(), key=str.casefold)
    skill_text = str(person.get("skills_search") or "").strip()

    tags = "".join(f"<span>{escape(skill)}</span>" for skill in skills)
    skills_html = (
        f'<div class="tags">{tags}</div>' if tags
        else f"<p>{escape(skill_text or 'No skills reported.')}</p>"
    )

    photo = photo_url(person)
    initials = "".join(word[0].upper() for word in name.split()[:2]) or "VE"
    image = (
        f'<img src="{escape(photo)}" alt="" loading="lazy" referrerpolicy="no-referrer">'
        if photo else ""
    )
    experience = f"{years:g} years of experience" if years is not None else "Experience not reported"
    search_text = " ".join([name, role, category, availability, *skills, skill_text])

    return f"""
    <article class="card" data-search="{escape(search_text)}">
      <div class="profile">
        <div class="avatar" aria-hidden="true">{escape(initials)}{image}</div>
        <div>
          <h2>{escape(name)}</h2>
          <p class="muted">{escape(role)} · {escape(category)}</p>
        </div>
      </div>
      <div class="facts">
        <span>{escape(experience)}</span>
        <strong>{hours:g} bench hours</strong>
      </div>
      <p class="muted">API status: {escape(availability)}</p>
      <details>
        <summary>View profile details</summary>
        <h3>Professional summary</h3>
        <p class="bio">{escape(bio or 'No summary provided.')}</p>
        <h3>Skills</h3>
        {skills_html}
      </details>
    </article>
    """


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bench Directory · Virtual Employee</title>
<style>
  :root {
    font: 16px/1.5 system-ui, sans-serif;
    color: #142c28; background: #f3faf8;
    --teal: #007565; --line: #d7e7e2; --muted: #526b64;
  }
  * { box-sizing: border-box; }
  body { margin: 0; }
  header { padding: 16px max(24px, calc((100vw - 1160px)/2)); background: white;
           border-bottom: 1px solid var(--line); font-weight: 750; }
  main { max-width: 1208px; padding: 28px 24px 48px; margin: auto; }
  h1 { margin: 6px 0; font-size: clamp(1.9rem, 5vw, 3rem); letter-spacing: -.045em; }
  p { margin: 5px 0; }
  .muted { color: var(--muted); font-size: .9rem; }
  .toolbar { display: flex; flex-wrap: wrap; align-items: end; gap: 10px 18px; margin: 22px 0 16px; }
  label { display: grid; gap: 5px; width: min(100%, 480px); font-weight: 650; }
  input { padding: 10px 12px; border: 1px solid #a9c5be; border-radius: 8px; font: inherit; }
  input:focus-visible, summary:focus-visible { outline: 2px solid var(--teal); outline-offset: 3px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 330px), 1fr));
          gap: 14px; align-items: start; }
  .card { padding: 17px; border: 1px solid var(--line); border-radius: 14px; background: white;
          overflow-wrap: anywhere; }
  .profile, .facts { display: flex; align-items: center; gap: 12px; }
  .facts { flex-wrap: wrap; justify-content: space-between; margin: 14px 0 5px; font-size: .9rem; }
  .facts strong { color: var(--teal); }
  .avatar { position: relative; display: grid; place-items: center; height: 54px; width: 54px;
            flex: 0 0 54px; border-radius: 50%; overflow: hidden; color: var(--teal);
            background: #e5f8f3; font-weight: 750; }
  .avatar img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
  h2 { margin: 0; font-size: 1.08rem; }
  h3 { margin: 12px 0 4px; font-size: .93rem; }
  details { margin-top: 12px; border-top: 1px solid var(--line); padding-top: 10px; }
  summary { cursor: pointer; color: var(--teal); font-weight: 700; }
  .bio { white-space: pre-wrap; }
  .tags { display: flex; flex-wrap: wrap; gap: 5px; }
  .tags span { padding: 3px 8px; border-radius: 6px; background: #e5f8f3; font-size: .8rem; }
  [hidden] { display: none !important; }
</style>
</head>
<body>
<header>Virtual Employee · Internal Bench Viewer</header>
<main>
  <h1>Bench directory</h1>
  <p class="muted">Shows profiles with positive bench hours at the time the script was run.
    Reported hours are not a booking or availability guarantee.</p>
  <div class="toolbar">
    <label>Search profiles
      <input id="search" type="search" placeholder="Name, designation, category, or skill">
    </label>
    <span id="count" class="muted" aria-live="polite"></span>
  </div>
  <p id="empty" hidden>No profiles to show.</p>
  <div class="grid">__CARDS__</div>
</main>
<script>
  const search = document.getElementById("search");
  const cards = Array.from(document.querySelectorAll(".card"));

  document.addEventListener("error", (event) => {
    if (event.target.matches?.(".avatar img")) event.target.remove();
  }, true);

  function filter() {
    const terms = search.value.trim().toLowerCase().split(/\\s+/).filter(Boolean);
    let visible = 0;
    for (const card of cards) {
      const text = card.dataset.search.toLowerCase();
      card.hidden = !terms.every(term => text.includes(term));
      if (!card.hidden) visible++;
    }
    document.getElementById("count").textContent = `${visible} of ${cards.length} profiles`;
    document.getElementById("empty").hidden = visible !== 0;
  }
  search.addEventListener("input", filter);
  filter();
</script>
</body>
</html>
"""


def serve_page(page):
    body = page.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                self.send_error(403)
                return
            if self.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if self.path != "/":
                self.send_error(404)
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    # Port 0 lets the OS pick a free port so this won't clash with Hiring on 8000.
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"\nOpen: {url}\nPress Ctrl+C to stop the viewer.")
        webbrowser.open(url)
        server.serve_forever()


def main():
    key = BENCH_API_KEY.strip()
    if not key or key == "PASTE_YOUR_CURRENT_BENCH_API_KEY_HERE":
        raise ValueError("Set BENCH_API_KEY at the top of bench_viewer.py.")

    profiles, warning = fetch_profiles(BENCH_API_URL, key)
    benched = [
        person for person in profiles
        if (number(person.get("bench_hours")) or 0) > 0
    ]
    benched.sort(key=lambda person: str(person.get("name") or "").casefold())

    cards = "".join(render_card(person) for person in benched)
    page = PAGE
    if warning:
        print(f"Warning: {warning}")
        page = page.replace(
            "<h1>Bench directory</h1>",
            f'<h1>Bench directory</h1><p role="alert"><strong>{escape(warning)}</strong></p>',
            1,
        )

    print(f"Showing {len(benched)} profiles with positive bench hours.")
    serve_page(page.replace("__CARDS__", cards, 1))


if __name__ == "__main__":
    try:
        main()
    except HTTPError as error:
        raise SystemExit(f"Bench API returned HTTP {error.code}. Check the URL and key access.")
    except (URLError, TimeoutError):
        raise SystemExit("Could not reach the Bench API. Check the URL and your network.")
    except ValueError as error:
        raise SystemExit(f"Could not load the directory: {error}")
    except KeyboardInterrupt:
        print("\nViewer stopped.")