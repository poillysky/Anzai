import { redirect } from "next/navigation";

/** Legacy 看盘 path → 新闻 */
export default function WatchRedirect() {
  redirect("/news");
}
