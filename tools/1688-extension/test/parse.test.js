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
{  // names worded differently on the two sides (the user's 油壶)
  const oil = ['水系油壶 1000ml 香槟色', '水系油壶 1000ml 烟灰色', '水系油壶 1000ml 透明色',
    '水系油壶连帽 1000ml 香槟色', '水系油壶连帽 1000ml 烟灰色', '水系油壶连帽 1000ml 透明色'].map((name) => ({ name }))
  const mine = [['a', '细条纹油壶1000ml香槟色'], ['b', '细条纹油壶1000ml烟灰色'], ['c', '细条纹油壶(连帽款)1000ml香槟色'],
    ['d', '细条纹油壶(连帽款)1000ml烟灰色'], ['e', '细条纹油壶1000ml'], ['f', '细条纹油壶(连帽款)1000ml']].map(([key, label]) => ({ key, label }))
  const m = P.matchSpecs(mine, oil)
  assert.deepStrictEqual([m.a, m.b, m.c, m.d], [0, 1, 3, 4])
  assert.ok([0, 1, 2].includes(m.e), 'no 连帽 for the plain one: ' + m.e)
  assert.ok([3, 4, 5].includes(m.f), '连帽 for the 连帽 one: ' + m.f)
  // nothing alike: not matched
  assert.deepStrictEqual(P.matchSpecs([{ key: 'x', label: '细条纹油壶1000ml' }, { key: 'y', label: '香槟色' }],
    [{ name: '马卡龙色【升级四层+加固横梁】' }, { name: '奶油白色【升级四层+加固横梁】' }, { name: '黑色' }]), { x: -1, y: -1 })
}

const best = P.pickBest([{ verdict: '同款', sales: 40, price: 39.9 }, { verdict: '同款', sales: 1000, price: 45 },
  { verdict: '相似', price: 10 }])
assert.strictEqual(best.sales, 1000)
assert.strictEqual(P.pickBest([{ verdict: '相似', price: 12 }, { verdict: '相似', price: 9 }, { verdict: '不同', price: 1 }]).price, 9)
// offers from the page's own search responses
{
  const resp = { data: { result: { offerList: [
    { offerId: 650001234567, subject: '跨境<font>牛皮纸</font>果冻胶', imageUrl: '//cbu01.alicdn.com/img/a.jpg', price: '0.08', saleQuantity: '10万+' },
    { id: 1, detailUrl: 'https://detail.1688.com/offer/650009999999.html', offerPicUrl: 'https://cbu01.alicdn.com/b.jpg', priceInfo: { price: '2.60' }, soldOut: 4500, title: '瑜伽袜' },
    { offerId: 650001234567, imageUrl: 'x' },  // duplicate
    { name: 'not an offer', imageUrl: 'https://x/y.jpg' },
  ] } } }
  const offers = P.offersFromJson(resp)
  assert.deepStrictEqual(offers.map((o) => [o.key, o.price, o.sales]), [['650001234567', 0.08, 100000], ['650009999999', 2.6, 4500]])
  assert.strictEqual(offers[0].title, '跨境牛皮纸果冻胶')
  assert.strictEqual(offers[0].image_url, 'https://cbu01.alicdn.com/img/a.jpg')
  assert.strictEqual(offers[1].url, 'https://detail.1688.com/offer/650009999999.html')
}
{
  const offers = P.offersFromJson({ list: [{ itemId: '710001112223', title: '玻璃油壶', imgUrl: 'https://cbu01.alicdn.com/c.jpg', price: 3.5 },
    { id: 720001112223, subject: '置物架', image: { imgUrl: '//cbu01.alicdn.com/d.jpg' }, priceInfo: { price: '12' } },
    { id: 123456789, name: '分类', imageUrl: 'https://x/cat.jpg' }] })  // a category, not an offer
  assert.deepStrictEqual(offers.map((o) => o.key), ['710001112223', '720001112223'])
}
console.log('parse.js: all checks passed')
