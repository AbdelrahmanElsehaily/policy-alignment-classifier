/** Readable names for eval cases. Ids stay the stable reference; names are for people. */
import titles from "../../data/ui/case_titles.json";

const TITLES: Record<string, string> = titles;

export function titleOf(id: string): string {
  return TITLES[id] ?? id;
}
