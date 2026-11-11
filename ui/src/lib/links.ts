/** Fluidd is served from the root of the same web server; Valve Oil UI lives under /valve-oil/. */
export function fluiddUrl(path = ''): string {
  return `${location.origin}/${path.replace(/^\//, '')}`
}
