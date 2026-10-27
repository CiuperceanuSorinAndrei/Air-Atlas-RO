import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

test('React and ReactDOM initialize with compatible runtime versions', () => {
  assert.equal(
    renderToStaticMarkup(createElement('p', null, 'Atlas runtime')),
    '<p>Atlas runtime</p>',
  )
})
