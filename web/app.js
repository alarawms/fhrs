const TIER_MEANINGS = {
  A: 'explicit — record names the country',
  B: 'coordinates inside land or EEZ',
  C: 'inferred from a national institution',
}

async function getJson(url) {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${url} → ${res.status}`)
  return res.json()
}

function row(tbody, cells) {
  const tr = document.createElement('tr')
  for (const c of cells) {
    const td = document.createElement('td')
    td.textContent = c
    tr.appendChild(td)
  }
  tbody.appendChild(tr)
}

async function main() {
  const status = document.getElementById('status')
  try {
    const config = await getJson('/config')
    document.title = config.branding.site_name
    document.getElementById('site-name').textContent = config.branding.site_name
    document.getElementById('local-name').textContent = config.name_local
    document.getElementById('tagline').textContent = config.branding.tagline
    document.getElementById('mark').textContent = `FHRS · ${config.country_code}`

    const stats = await getJson('/api/v1/stats')
    const s = stats.samples
    document.getElementById('n-total').textContent = s.public.toLocaleString()
    document.getElementById('n-studies').textContent = stats.studies.toLocaleString()
    document.getElementById('n-coords').textContent = s.with_coordinates.toLocaleString()
    document.getElementById('n-marine').textContent = s.marine_eez.toLocaleString()
    document.getElementById('n-curation').textContent = s.in_curation.toLocaleString()
    document.getElementById('n-dropped').textContent = stats.dropped_out_of_scope.toLocaleString()
    document.getElementById('cards').hidden = false

    const tiers = document.getElementById('tiers')
    for (const [tier, n] of Object.entries(s.by_tier)) {
      row(tiers, [tier, TIER_MEANINGS[tier] ?? '', n.toLocaleString()])
    }
    document.getElementById('tiers-section').hidden = false

    const buckets = document.getElementById('buckets')
    for (const [bucket, n] of Object.entries(s.by_content_type).sort((a, b) => b[1] - a[1])) {
      row(buckets, [bucket, n.toLocaleString()])
    }
    document.getElementById('buckets-section').hidden = false

    status.textContent =
      `snapshot built ${stats.built_at} · ${stats.source_events} source events` +
      (stats.unmapped_checklists.length
        ? ` · ${stats.unmapped_checklists.length} unmapped checklist(s) awaiting registry`
        : '')
  } catch (err) {
    status.textContent = `not ready: ${err.message}`
  }
}

main()
