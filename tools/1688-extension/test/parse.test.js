// node test/parse.test.js — checks the page-reading functions on sample 1688 page data.
const assert = require('assert')
const P = require('../parse.js')

assert.strictEqual(P.offerIdFromUrl('https://detail.1688.com/offer/812345678901.html?spm=a'), '812345678901')
assert.strictEqual(P.offerIdFromUrl('https://x.1688.com/page?offerId=723456789'), '723456789')

const script = 'window.__INIT_DATA={"globalData":{"tempModel":{"companyName":"江阴市慕格雅服饰商行"},' +
  '"skuModel":{"skuInfoMap":{"杏色加长款 建议身高158以上&gt;S 75-90":{"skuId":11,"price":"39.90","canBookCount":708},' +
  '"杏色加长款 建议身高158以上&gt;M 90-105":{"skuId":12,"price":"39.90","canBookCount":167},' +
  '"杏色小个子 建议身高158以下&gt;S 75-90":{"skuId":21,"discountPrice":"36.50","canBookCount":5}}}}}'
const skus = P.skusFromScripts(script, 39.9)
assert.strictEqual(skus.length, 3)
assert.deepStrictEqual(skus[0], { name: '杏色加长款 建议身高158以上 / S 75-90', sku_id: '11', price: 39.9, stock: 708 })
assert.strictEqual(skus[2].price, 36.5)
assert.strictEqual(P.stringAfter(script, 'companyName'), '江阴市慕格雅服饰商行')

assert.strictEqual(P.freightFromText('广东深圳 送至 广东深圳 | 运费¥5.5起'), 5.5)
assert.strictEqual(P.freightFromText('另需运费：¥ 8'), 8)
assert.strictEqual(P.freightFromText('全国包邮'), 0)
assert.strictEqual(P.freightFromText('无'), null)
assert.strictEqual(P.salesFromText('已售40+件'), 40)
assert.strictEqual(P.salesFromText('全网销量200+件'), 200)
assert.strictEqual(P.salesFromText('已售1.2万'), 12000)
assert.strictEqual(P.priceFromText('¥39.90 1件起批'), 39.9)

const ours = [{ key: 'k1', label: '杏色加长款 / S 75-90' }, { key: 'k2', label: '杏色加长款 / M 90-105' },
  { key: 'k3', label: '杏色小个子 / S 75-90' }]
assert.deepStrictEqual(P.matchSpecs(ours, skus), { k1: 0, k2: 1, k3: 2 })
assert.deepStrictEqual(P.matchSpecs([{ key: 'a', label: '默认' }], [{ name: 'x', price: 1 }]), { a: 0 })

const best = P.pickBest([{ verdict: '同款', sales: 40, price: 39.9 }, { verdict: '同款', sales: 1000, price: 45 },
  { verdict: '相似', price: 10 }])
assert.strictEqual(best.sales, 1000)
assert.strictEqual(P.pickBest([{ verdict: '相似', price: 12 }, { verdict: '相似', price: 9 }, { verdict: '不同', price: 1 }]).price, 9)
console.log('parse.js: all checks passed')
