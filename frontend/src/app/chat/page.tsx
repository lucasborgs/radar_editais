import { redirect } from "next/navigation";

/** Deep-links antigos não criam mais WritingSessions por edital. */
export default function ChatPage() {
  redirect("/");
}
