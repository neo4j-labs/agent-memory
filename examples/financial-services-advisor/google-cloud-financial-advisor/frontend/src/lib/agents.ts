/**
 * The five Google ADK agents this app runs, and how they are presented.
 *
 * Kept in one place so the live orchestration view, the post-completion
 * timeline and the tool-call cards agree on labels and colour coding.
 */
export const AGENT_LABELS: Record<string, string> = {
  supervisor: "Supervisor",
  kyc_agent: "KYC Agent",
  aml_agent: "AML Agent",
  relationship_agent: "Relationship Agent",
  compliance_agent: "Compliance Agent",
};

/** Chakra `colorPalette` per agent. */
export const AGENT_COLORS: Record<string, string> = {
  supervisor: "blue",
  kyc_agent: "teal",
  aml_agent: "orange",
  relationship_agent: "purple",
  compliance_agent: "red",
};

export const AGENT_DESCRIPTIONS: Record<string, string> = {
  supervisor: "Orchestrating investigation",
  kyc_agent: "Identity verification & due diligence",
  aml_agent: "Transaction monitoring & pattern detection",
  relationship_agent: "Network analysis & ownership tracing",
  compliance_agent: "Sanctions screening & regulatory checks",
};

export function agentLabel(name: string): string {
  return AGENT_LABELS[name] ?? name;
}

export function agentColor(name: string): string {
  return AGENT_COLORS[name] ?? "gray";
}
