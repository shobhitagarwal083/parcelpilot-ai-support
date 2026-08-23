import type { Citation, Tier } from "../lib/types";

const TIER_NAMES: Record<Tier, string> = {
  1: "AGREEMENT",
  2: "POLICY",
  3: "PRODUCT DOC",
  4: "HISTORICAL",
};

/** Tier 4 is drawn with a dashed border on purpose.
 *
 * Historical ticket resolutions are context, never authority -- two of them in
 * this dataset assert answers the current documents contradict. The system has
 * to be able to show one without appearing to endorse it, so the badge has to
 * read differently at a glance, not just carry a different number.
 */
export function TierBadge({ tier }: { tier: Tier }) {
  return (
    <span className={`tier tier-${tier}`} title={`Authority tier ${tier}`}>
      T{tier} {TIER_NAMES[tier]}
    </span>
  );
}

export function CitationCard({ citation }: { citation: Citation }) {
  return (
    <div className="citation">
      <div className="citation-head">
        <TierBadge tier={citation.authority_tier} />
        <span className="citation-doc">{citation.doc_title}</span>
        <span className="citation-section">{citation.section}</span>
      </div>
      <blockquote className="citation-quote">{citation.quote}</blockquote>
    </div>
  );
}
