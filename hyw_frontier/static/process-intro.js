// Keep introductions from different rounds; explicit text replaces only its own round's fallback.
// Never expose reasoning events or an unfinished/final-answer round as an introduction.
export function createProcessIntro(container) {
  const entries = [];
  const seen = new Set();
  const pending = new Map();
  let round = null;
  const show = () => {
    container.textContent = entries.map(entry => entry.text).join("\n\n");
    container.hidden = entries.length === 0;
  };
  return {
    clear() {
      entries.length = 0;
      seen.clear();
      pending.clear();
      round = null;
      show();
    },
    accept(event) {
      if (event.type === "model_start") {
        round = Number.isInteger(event.round) ? event.round : null;
        pending.clear();
      }
      if (event.type === "process_intro") {
        if (typeof event.text !== "string" || !event.text.trim()) return;
        const eventRound = Number.isInteger(event.round) ? event.round : round;
        const key = typeof event.id === "string" && event.id ? event.id
          : `round:${eventRound}:supplemental:${event.supplemental === true}`;
        if (seen.has(key)) return;
        seen.add(key);
        const entry = { text: event.text, round: eventRound, explicit: true };
        const fallback = entries.findIndex(item => !item.explicit && item.round === eventRound);
        if (fallback >= 0) entries[fallback] = entry;
        else entries.push(entry);
        pending.clear();
        show();
        return;
      }
      // Ordinary text is only a first-introduction fallback, not a second broadcast channel.
      if (entries.length) return;
      if (event.type === "error" || event.type === "cancelled") pending.clear();
      if (event.type === "text_end" && Number.isInteger(event.index) && typeof event.content === "string") {
        pending.set(event.index, event.content);
      }
      if (event.type === "model_stream_end") {
        const text = [...pending.entries()].sort(([a], [b]) => a - b).map(([, text]) => text).join("\n\n").trim();
        pending.clear();
        if (event.reason === "toolUse" && text) {
          entries.push({ text, round: Number.isInteger(event.round) ? event.round : round, explicit: false });
          show();
        }
      }
    },
  };
}
