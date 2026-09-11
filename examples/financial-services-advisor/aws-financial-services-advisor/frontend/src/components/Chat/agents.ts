import type { ElementType } from 'react'
import { LuBrain, LuFileCheck, LuNetwork, LuSearch, LuShield } from 'react-icons/lu'

export interface AgentConfig {
  label: string
  icon: ElementType
  /** Chakra colour palette used for badges, borders and card accents. */
  colorPalette: string
  description: string
}

/**
 * The supervisor and its four specialists. Both the bare and the `_agent`
 * spelling are accepted because Strands names the delegation tools
 * `delegate_to_<name>_agent`.
 */
const AGENTS: Record<string, AgentConfig> = {
  supervisor: {
    label: 'Supervisor',
    icon: LuBrain,
    colorPalette: 'brand',
    description: 'Orchestrating investigation',
  },
  kyc: {
    label: 'KYC Agent',
    icon: LuFileCheck,
    colorPalette: 'teal',
    description: 'Identity verification',
  },
  aml: {
    label: 'AML Agent',
    icon: LuSearch,
    colorPalette: 'orange',
    description: 'Transaction monitoring',
  },
  relationship: {
    label: 'Relationship Agent',
    icon: LuNetwork,
    colorPalette: 'purple',
    description: 'Network analysis',
  },
  compliance: {
    label: 'Compliance Agent',
    icon: LuShield,
    colorPalette: 'red',
    description: 'Regulatory compliance',
  },
}

export function agentConfig(name: string): AgentConfig {
  const key = name.toLowerCase().replace(/_agent$/, '')
  return (
    AGENTS[key] ?? {
      label: name,
      icon: LuBrain,
      colorPalette: 'gray',
      description: '',
    }
  )
}
