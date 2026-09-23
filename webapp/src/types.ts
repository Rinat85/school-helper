/** Формы ответов API — зеркало src/schoolhelper/api/*.py. */

export type Permission =
  | 'money.view_by_person'
  | 'collection.create'
  | 'payment.confirm'
  | 'expense.approve'
  | 'poll.create'
  | 'case.close'
  | 'audit.view'
  | 'role.grant'
  | 'person.manage'
  | 'class.edit'

export type Role = 'parent' | 'treasurer' | 'chair' | 'auditor' | 'teacher' | 'admin'
export type AssignableRole = 'treasurer' | 'chair' | 'auditor' | 'teacher'

export interface Me {
  person: { id: number; name: string; child: string | null }
  roles: Role[]
  class: { id: number; name: string; school: string | null; currency: string }
  can: Record<Permission, boolean>
  sees_money: boolean
}

export type ContributionStatus = 'confirmed' | 'partial' | 'claimed' | 'waived' | 'pending'

export interface Home {
  payments_to_confirm?: number
  not_connected?: number
  my_open_contributions: {
    collection_id: number
    title: string
    expected: number
    paid: number
    due_date: string | null
    status: ContributionStatus
  }[]
}

export interface Progress {
  collected: number
  target: number
  people: number
  percent: number
}

export interface Collection extends Progress {
  id: number
  title: string
  purpose: string | null
  amount_per_person: number
  due_date: string | null
  payment_code: string | null
  status: 'open' | 'closed'
  created_at: string
  closed_at: string | null
}

export interface RosterRow {
  person_id: number
  name: string
  child: string | null
  expected: number
  paid: number
  bot_connected: boolean
  status: ContributionStatus
}

export interface CollectionDetail extends Collection {
  mine: { expected: number; paid: number; status: ContributionStatus } | null
  roster?: RosterRow[]
}

export interface MoneySummary {
  balance: number
  inflow: number
  outflow: number
  collections: ({ id: number; title: string; due_date: string | null } & Progress)[]
}

export interface Person {
  id: number
  name: string
  child: string | null
  username: string | null
  in_telegram: boolean
  bot_connected: boolean
  status: 'active' | 'left'
  roles: AssignableRole[]
  is_admin: boolean
}

export interface Invite {
  link: string
  not_connected: { id: number; name: string }[]
}

export interface Settings {
  name: string
  school: string | null
  card_number: string | null
  card_holder: string | null
  currency: string
  group_bound: boolean
}

export interface PendingPayment {
  id: number
  person_id: number
  name: string
  amount: number
  method: 'cash' | 'transfer'
  collection_id: number
  collection_title: string
  claimed_at: string
  receipt_id: number | null
}
