type IconName = 'ask' | 'knowledge' | 'person' | 'import'

const paths: Record<IconName, React.ReactNode> = {
  ask: <><path d="M5.5 4.5h13a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H11l-4.5 3v-3h-1a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2Z" /><path d="M8 9.5h8M8 12.5h5" /></>,
  knowledge: <><path d="M4.5 5.5a2 2 0 0 1 2-2H18a1.5 1.5 0 0 1 1.5 1.5v14.5H7a2.5 2.5 0 0 1-2.5-2.5V5.5Z" /><path d="M4.5 17a2.5 2.5 0 0 1 2.5-2.5h12.5M9 7.5h6M9 10.5h5" /></>,
  person: <><circle cx="12" cy="8" r="3.25" /><path d="M5.5 19a6.5 6.5 0 0 1 13 0" /></>,
  import: <><path d="M12 15V3.5M8.5 7 12 3.5 15.5 7" /><path d="M5 14.5v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4" /></>,
}

export default function NavigationIcon({ name }: { name: IconName }) {
  return <svg className="nav-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>
}
