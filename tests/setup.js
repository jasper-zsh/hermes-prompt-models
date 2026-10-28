import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'
afterEach(cleanup)
Element.prototype.scrollIntoView = () => {}
Element.prototype.hasPointerCapture = () => false
Element.prototype.setPointerCapture = () => {}
Element.prototype.releasePointerCapture = () => {}
window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }
window.PointerEvent = window.MouseEvent
window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })
