import { Cover } from "@/components/Cover";

/**
 * The root URL is the cover page. `/?dev=1`, the scripted demo the README and judge mode link to, goes on
 * to `/map?dev=1` (a redirect in next.config.ts), where the board is.
 */
export default function Home() {
  return <Cover />;
}
