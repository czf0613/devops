import { flushPromises } from '@vue/test-utils'

export function dialogButton(label: string) {
  return [...document.querySelectorAll<HTMLButtonElement>('dialog[open] button')].find(button => button.textContent?.trim() === label)!
}
export async function confirmWithCode(code = '123456') {
  const dialog = document.querySelector<HTMLDialogElement>('dialog[open]')!
  const input = dialog.querySelector<HTMLInputElement>('input[autocomplete="one-time-code"]')!
  input.value = code
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
  dialog.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  await flushPromises()
}
