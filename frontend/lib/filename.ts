/** Strip the internal .md suffix from a stored filename for display. */
export function displayFilename(name: string): string {
  return name.replace(/\.md$/, '')
}
