export type FieldType =
  | 'string'
  | 'integer'
  | 'decimal'
  | 'currency'
  | 'date'
  | 'datetime'
  | 'boolean'
  | 'email'
  | 'phone'
  | 'address'
  | 'iban'
  | 'tax_number'
  | 'array'
  | 'object'

export interface RuleSpec {
  type: string
  params: Record<string, unknown>
  severity: 'FAIL' | 'WARNING' | 'REQUIRES_HUMAN'
  message: string | null
}

export interface FieldDefinition {
  name: string
  type: FieldType
  required: boolean
  description: string
  aliases: string[]
  extraction_hints: { patterns: string[]; position: string }
  validation_rules: RuleSpec[]
  confidence_threshold: number
  normalization: Record<string, unknown>
  children: FieldDefinition[]
  item: FieldDefinition | null
}

export interface SchemaDefinition {
  fields: FieldDefinition[]
  rules: RuleSpec[]
  classification: {
    keywords: string[]
    negative_keywords: string[]
    first_page_markers: string[]
    min_score: number
  }
}

export interface DocumentTypeSummary {
  id: string
  key: string
  name: string
  description: string
  is_active: boolean
  project_id: string | null
  created_at: string
  published_version: number | null
  draft_version: number | null
}

export interface SchemaVersionSummary {
  id: string
  version: number
  status: 'draft' | 'published' | 'retired'
  created_at: string
  published_at: string | null
}

export interface SchemaVersion extends SchemaVersionSummary {
  definition: SchemaDefinition
}

export interface DocumentTypeDetail extends Omit<DocumentTypeSummary, 'published_version' | 'draft_version'> {
  versions: SchemaVersionSummary[]
}

export interface Template {
  key: string
  name: string
  description: string
  field_count: number
  definition: SchemaDefinition
}

/** Leaf + container paths, mirroring SchemaDefinition.flatten() on the server. */
export function flatten(fields: FieldDefinition[], prefix = ''): { path: string; field: FieldDefinition }[] {
  const out: { path: string; field: FieldDefinition }[] = []
  for (const field of fields) {
    if (field.type === 'array') {
      const path = `${prefix}${field.name}[]`
      out.push({ path, field })
      if (field.item?.type === 'object') out.push(...flatten(field.item.children, `${path}.`))
    } else if (field.type === 'object') {
      const path = `${prefix}${field.name}`
      out.push({ path, field })
      out.push(...flatten(field.children, `${path}.`))
    } else {
      out.push({ path: `${prefix}${field.name}`, field })
    }
  }
  return out
}
