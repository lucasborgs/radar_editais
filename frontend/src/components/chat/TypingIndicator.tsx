/**
 * Three bouncing dots shown while awaiting an assistant response.
 * Shared between the KG chat (dashboard) and the writing chat.
 */
export function TypingIndicator() {
  return (
    <div className="flex items-center gap-1 px-1 py-0.5">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="w-1.5 h-1.5 bg-content-secondary rounded-full animate-bounce"
          style={{ animationDelay: `${i * 0.15}s` }}
        />
      ))}
    </div>
  );
}
