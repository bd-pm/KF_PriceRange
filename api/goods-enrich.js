// Proxies GlobalBunjang's get-summaries API to fetch English product names by pid.
// Kept server-side because the upstream API needs a specific client header and
// calling it directly from the browser would hit CORS.

export default async function handler(req, res) {
  const raw = req.query.pids;
  if (!raw) return res.status(400).json({ error: "pids required" });

  const pids = String(raw)
    .split(",")
    .map((p) => parseInt(p, 10))
    .filter((p) => Number.isFinite(p));

  if (!pids.length) return res.status(200).json({});

  try {
    const upstream = await fetch(
      "https://api.globalbunjang.com/api/global-pms/v1/products/get-summaries",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Client-ID": "bun-web-mobile-global",
        },
        body: JSON.stringify({ pids }),
      }
    );

    if (!upstream.ok) {
      return res.status(502).json({ error: `upstream ${upstream.status}` });
    }

    const body = await upstream.json();
    const map = {};
    for (const item of body.data || []) {
      if (item.pid != null && item.nameEng) map[item.pid] = item.nameEng;
    }

    res.setHeader("Cache-Control", "s-maxage=3600, stale-while-revalidate=86400");
    return res.status(200).json(map);
  } catch (e) {
    return res.status(502).json({ error: "upstream request failed" });
  }
}
