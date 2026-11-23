<template>
  <div class="wrap">
    <!-- stores -->
    <div class="card stores">
      <div class="card-head"><div class="card-title">店铺</div>
        <el-button size="small" type="primary" :disabled="!stores.length" @click="sync(stores.map((s) => s.id))">全部同步</el-button>
      </div>
      <div class="store" :class="{ active: storeId === null }" @click="pick(null)">
        <div class="b">全部店铺</div><div class="muted">{{ stores.reduce((a, s) => a + s.count, 0) }} 个商品</div>
      </div>
      <div v-for="s in stores" :key="s.id" class="store" :class="{ active: storeId === s.id }" @click="pick(s.id)">
        <div class="row">
          <el-avatar :src="s.logo" :size="26">{{ s.name.slice(0, 1) }}</el-avatar>
          <div class="grow"><div class="b ellipsis">{{ s.name }}</div>
            <div class="muted">{{ s.count }} 个 · 在售 {{ s.on_sale }}</div></div>
          <el-button link type="primary" size="small" :loading="s.sync_status === 'running'" @click.stop="sync([s.id])">同步</el-button>
        </div>
        <div class="muted small" :class="{ danger: s.sync_status === 'failed' }">
          {{ s.sync_status === 'running' ? s.sync_message : s.sync_status === 'failed' ? s.sync_message : s.synced_at ? `上次同步 ${s.synced_at}` : '未同步' }}
        </div>
      </div>
    </div>

    <!-- products -->
    <div class="main">
      <div class="card">
        <el-form :inline="true">
          <el-form-item label="搜索"><el-input v-model="q.keyword" placeholder="标题 / 商品ID / 商品编码" clearable style="width: 240px" @keyup.enter="search" /></el-form-item>
          <el-form-item label="状态">
            <el-select v-model="q.status" placeholder="全部" clearable style="width: 140px" @change="search">
              <el-option label="上架" :value="5" /><el-option label="已下架" :value="11" /><el-option label="草稿箱" :value="0" />
              <el-option label="违规下架" :value="13" />
            </el-select>
          </el-form-item>
          <el-form-item label="货源">
            <el-select v-model="q.source" placeholder="全部" clearable style="width: 130px" @change="search">
              <el-option label="已设货源" value="linked" /><el-option label="无货源" value="none" /><el-option label="未找货源" value="unsearched" />
            </el-select>
          </el-form-item>
          <el-form-item label="上架日期">
            <el-date-picker v-model="listedDates" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始" end-placeholder="结束" style="width: 240px" @change="search" />
          </el-form-item>
          <el-form-item label="排序">
            <el-select v-model="q.sort" style="width: 130px" @change="search">
              <el-option label="默认" value="" /><el-option label="上架日期（新→旧）" value="listed" /><el-option label="近7天点击（多→少）" value="clicks" /><el-option label="近30天曝光（多→少）" value="exposure" />
            </el-select>
          </el-form-item>
          <el-form-item><el-button type="primary" icon="Search" @click="search">查询</el-button></el-form-item>
        </el-form>
      </div>
      <div class="card">
        <div class="card-head">
          <div class="card-title">商品列表
            <span class="muted">（共 {{ total }} 个 · </span><el-link type="warning" :underline="false" @click="showUnsearched">未找货源 {{ unsearchedCount }} 个</el-link><span class="muted">）</span>
          </div>
          <div>
            <el-button :disabled="!selected.length" @click="batchVisible = true">批量改价</el-button>
            <el-tooltip :content="selected.length ? `给选中的 ${selected.length} 个商品按公式改价` : '没有勾选时：当前列表的全部商品（自动跳过没有货源的）'" placement="top">
              <el-button type="warning" plain @click="openFormula(selected)">公式改价{{ selected.length ? `（${selected.length}）` : '（全部）' }}</el-button>
            </el-tooltip>
            <el-tooltip content="1688 货源助手里「设为货源」后，自动按预设公式给这个商品改价（同步到微信小店），不用再打开公式改价" placement="top">
              <el-button :type="ap.enabled ? 'success' : 'default'" plain @click="apVisible = true">
                自动改价：{{ ap.enabled ? `开（×${ap.margin}${ap.add ? ` +¥${ap.add}` : ''}）` : '关' }}</el-button>
            </el-tooltip>
            <el-tooltip :content="storeId ? '读取这个店铺正在生效的优惠券，用于计算到手价（同步商品时也会读取）' : '先在左侧选择一个店铺'" placement="top">
              <span><el-button :disabled="!storeId" :loading="couponSyncing" @click="syncCoupons">同步优惠券</el-button></span>
            </el-tooltip>
            <el-button :disabled="!selected.length" @click="stockBatchVisible = true">批量改库存</el-button>
            <SourcingStats />
            <el-tooltip content="把微信小店草稿箱里的商品全部提交上架（会先经过微信审核，审核通过后上架）" placement="top">
              <el-button type="success" :disabled="!draftCount" :loading="publishing" @click="publishDrafts">{{ publishing ? publishText : `发布草稿箱（${draftCount}）` }}</el-button>
            </el-tooltip>
            <el-button :disabled="!selected.length" @click="act('listing')">批量上架</el-button>
            <el-button :disabled="!selected.length" @click="act('delisting')">批量下架</el-button>
            <el-button type="danger" plain :disabled="!selected.length" @click="act('delete')">批量删除</el-button>
            <el-button type="danger" :disabled="!noSourceCount" :loading="purging" @click="deleteNoSource">删除全部无货源商品（{{ noSourceCount }}）</el-button>
          </div>
        </div>
        <el-table :data="items" v-loading="loading" border @selection-change="(v: any[]) => (selected = v)">
          <el-table-column type="selection" width="45" align="center" />
          <el-table-column label="主图" width="90" align="center">
            <template #default="{ row }"><el-image :src="row.cover" fit="cover" style="width: 64px; height: 64px; border-radius: 4px" :preview-src-list="[row.cover]" preview-teleported lazy /></template>
          </el-table-column>
          <el-table-column label="商品" min-width="240">
            <template #default="{ row }">
              <div class="two clickable-title" title="点击预览商品详情页" @click="previewRef?.open(row.store_id, row.wx_product_id)">{{ row.title }}</div>
              <div class="muted small">{{ row.store_name }}</div>
            </template>
          </el-table-column>
          <el-table-column label="商品ID / 编码" min-width="170">
            <template #default="{ row }"><div>{{ row.wx_product_id }}</div><div class="muted small">{{ row.spu_code || '-' }}</div></template>
          </el-table-column>
          <el-table-column label="零售价" width="160" align="right">
            <template #default="{ row }">
              <div>{{ priceRange(row.price_min, row.price_max) }}</div>
              <el-tooltip v-if="row.library_price_min != null" content="商品云库里这个商品的原价（数据包里的来源售价）" placement="top">
                <div class="libprice">云库 {{ priceRange(row.library_price_min, row.library_price_max) }}</div>
              </el-tooltip>
              <el-tooltip v-if="row.deal_min != null" :content="`${row.coupon.type_name}「${row.coupon.name}」减 ¥${yuan(row.coupon.off)}`" placement="top">
                <div class="deal">到手 {{ priceRange(row.deal_min, row.deal_max) }}</div>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="货源价" width="180" align="center">
            <template #default="{ row }">
              <template v-if="row.source">
                <a :href="row.source.detail_url" target="_blank" :title="row.source.supplier">{{ priceRange(row.source.price_min ?? row.source.price, row.source.price_max ?? row.source.price) }}</a>
                <div v-if="(row.skus || []).length > 1" class="skusrc">
                  <div v-for="k in row.skus" :key="k.sku_id" class="skusrc-row">
                    <span class="ellipsis" :title="k.attrs">{{ specName(k.attrs) || k.sku_id }}</span>
                    <span :class="{ own: row.source.sku_prices && row.source.sku_prices[k.sku_id] != null }">¥{{ yuan(srcOf(row, k.sku_id)) }}</span>
                  </div>
                </div>
                <el-tooltip v-if="row.source.price_edited" :content="`货源价已手动修改，1688 原价 ¥${yuan(row.source.original_price)}`" placement="top"><el-tag type="warning" size="small" effect="plain" class="edited">已改价</el-tag></el-tooltip>
                <div class="small">
                  <span v-if="row.source.from_library" class="muted" title="在商品云库中设置的货源">云库 · </span>
                  <el-button link type="primary" size="small" @click="editSourcePrice(row, 'store_product_id')">改价</el-button>
                  <el-button link type="danger" size="small" @click="unlink(row)">取消</el-button>
                </div>
              </template>
              <el-tooltip v-else-if="row.source_state === 'none'" content="已找过货源，但未设为货源" placement="top"><el-tag type="danger" size="small">无货源</el-tag></el-tooltip>
              <span v-else class="muted">-</span>
            </template>
          </el-table-column>
          <el-table-column prop="stock" label="库存" width="80" align="right" />
          <el-table-column label="上架日期" width="104" align="center">
            <template #default="{ row }">
              <el-tooltip v-if="row.listed_at" :content="row.listed_approx ? '同步前就已上架：这是从本网站发布的时间（或第一次同步到的时间），不一定是实际上架时间' : `同步时发现上架：${row.listed_at}`" placement="top">
                <span :class="{ muted: row.listed_approx }">{{ row.listed_approx ? '≈' : '' }}{{ row.listed_at.slice(0, 10) }}</span>
              </el-tooltip>
              <span v-else class="muted">-</span>
            </template>
          </el-table-column>
          <el-table-column width="96" align="right">
            <template #header>
              <el-tooltip content="微信小店罗盘「商品点击次数 / 点击人数」近 7 天合计，每次同步商品时更新；曝光（近30天）由 1688 货源助手在小店后台商品列表读取" placement="top"><span>点击 / 曝光 ⓘ</span></el-tooltip>
            </template>
            <template #default="{ row }">
              <template v-if="row.clicks_7d != null"><b>{{ row.clicks_7d }}</b><div class="muted small">{{ row.click_uv_7d }} 人</div></template>
              <span v-else class="muted">-</span>
              <el-tooltip v-if="row.exposure_30d != null" :content="`近30天曝光（1688 货源助手在小店后台商品列表读取，${row.exposure_at}）`" placement="top">
                <div class="small" style="color: #ff6a00">曝光 {{ row.exposure_30d }}</div>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="120" align="center">
            <template #default="{ row }"><el-tag :type="row.status === 5 ? 'success' : row.status === 11 ? 'info' : 'warning'">{{ row.status_name }}</el-tag></template>
          </el-table-column>
          <el-table-column label="操作" width="330" align="center" fixed="right">
            <template #default="{ row }">
              <el-button link type="success" title="按微信小店商品详情页的样子显示：主图、价格、规格、详情图" @click="previewRef?.open(row.store_id, row.wx_product_id)">预览详情</el-button>
              <el-button link type="primary" @click="openPrice(row)">改价</el-button>
              <el-button link type="warning" @click="openFormula([row])">公式</el-button>
              <el-button link type="primary" @click="openStock(row)">改库存</el-button>
              <el-button v-if="row.status === 5" link type="primary" @click="act('delisting', [row])">下架</el-button>
              <el-button v-else link type="primary" @click="act('listing', [row])">上架</el-button>
              <el-button link type="danger" @click="act('delete', [row])">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="pager"><el-pagination v-model:current-page="q.page" v-model:page-size="q.size" :total="total" :page-sizes="[20, 50, 100]" layout="total, sizes, prev, pager, next" @change="load" /></div>
      </div>
    </div>

    <!-- single product price (per SKU) -->
    <el-dialog v-model="priceVisible" :title="`改价：${editing?.title || ''}`" width="640px">
      <el-table v-if="editing" :data="editing.skus" size="small" border>
        <el-table-column label="规格" prop="attrs" min-width="160" />
        <el-table-column label="SKU 编码" prop="sku_code" width="120" />
        <el-table-column label="当前价" width="90" align="right"><template #default="{ row }">¥{{ yuan(row.price) }}</template></el-table-column>
        <el-table-column label="新价格（元）" width="150">
          <template #default="{ row }"><el-input-number v-model="row._new" :min="0.01" :precision="2" :step="1" size="small" controls-position="right" style="width: 120px" /></template>
        </el-table-column>
      </el-table>
      <p class="muted">通过微信「免审更新商品」接口直接改价，已上架商品无需重新审核。</p>
      <template #footer><el-button @click="priceVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="savePrice">保存</el-button></template>
    </el-dialog>

    <!-- single product stock (per SKU) -->
    <el-dialog v-model="stockVisible" :title="`改库存：${stockEditing?.title || ''}`" width="600px">
      <el-table v-if="stockEditing" :data="stockEditing.skus" size="small" border>
        <el-table-column label="规格" prop="attrs" min-width="160" />
        <el-table-column label="SKU 编码" prop="sku_code" width="120" />
        <el-table-column label="当前库存" prop="stock" width="90" align="right" />
        <el-table-column label="新库存" width="150">
          <template #default="{ row }"><el-input-number v-model="row._new" :min="0" :precision="0" :step="10" size="small" controls-position="right" style="width: 120px" /></template>
        </el-table-column>
      </el-table>
      <p class="muted">保存后立即同步到微信小店（微信「快速更新库存」接口）。</p>
      <template #footer><el-button @click="stockVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveStock">保存</el-button></template>
    </el-dialog>

    <!-- batch stock -->
    <el-dialog v-model="stockBatchVisible" :title="`批量改库存（${selected.length} 个商品，所有规格）`" width="460px">
      <el-radio-group v-model="stockBatch.mode">
        <el-radio value="set">统一设为</el-radio><el-radio value="add">增加</el-radio><el-radio value="sub">减少</el-radio>
      </el-radio-group>
      <div style="margin-top: 14px">
        <span class="muted">{{ stockBatch.mode === 'set' ? '每个规格的库存设为' : stockBatch.mode === 'add' ? '每个规格增加' : '每个规格减少' }}</span>
        <el-input-number v-model="stockBatch.value" :min="0" :precision="0" :step="10" style="margin-left: 8px" />
        <span class="muted">件</span>
      </div>
      <p class="muted">会同步到微信小店。</p>
      <template #footer><el-button @click="stockBatchVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveStockBatch">确定</el-button></template>
    </el-dialog>

    <!-- batch price -->
    <!-- 自动改价: the preset 公式 used after 设为货源 -->
    <el-dialog v-model="apVisible" title="自动改价" width="520px">
      <el-form label-width="110px">
        <el-form-item label="设为货源后">
          <el-switch v-model="apForm.enabled" active-text="自动改价" inactive-text="不改价" />
        </el-form-item>
        <el-form-item label="新售价 = 货源价 ×">
          <el-input-number v-model="apForm.margin" :min="0.1" :max="100" :step="0.1" :precision="2" style="width: 140px" />
        </el-form-item>
        <el-form-item label="+ 加价（元）"><el-input-number v-model="apForm.add" :step="1" :precision="2" style="width: 140px" /></el-form-item>
        <el-form-item label="+ 优惠券金额"><span class="muted small">自动按店铺生效的优惠券计算（和公式改价一样）</span></el-form-item>
      </el-form>
      <p class="muted small">开启后，在 1688 货源助手里点「设为货源」时，网站按这个公式计算每个规格的新售价（货源价已含运费），直接改到微信小店；
        同一个商品云库商品在其他店铺里的商品也一起改。删除的规格不改价。</p>
      <template #footer>
        <el-button @click="apVisible = false">取消</el-button>
        <el-button type="primary" :loading="apSaving" @click="saveAutoPrice">保存</el-button>
      </template>
    </el-dialog>

    <!-- 公式改价 -->
    <el-dialog v-model="fVisible" :title="fRows.length ? `公式改价（${fRows.length} 个商品）` : `公式改价（当前列表全部商品，${fItems.length} 个有货源）`" width="1360px">
      <el-form :inline="true">
        <el-form-item label="新售价 = 货源价 ×">
          <el-input-number v-model="ff.margin" :min="0.1" :max="100" :step="0.1" :precision="2" style="width: 130px" />
        </el-form-item>
        <el-form-item label="+ 加价（元）"><el-input-number v-model="ff.add" :step="1" :precision="2" style="width: 130px" /></el-form-item>
        <el-form-item label="+ 优惠券金额"><span class="muted">自动按店铺生效的优惠券计算</span></el-form-item>
        <el-form-item><el-button type="primary" :loading="fLoading" @click="preview">计算</el-button></el-form-item>
      </el-form>
      <p class="muted small">每个规格单独计算：买家用券后的到手价 = 这个规格的货源价 × 倍率（+ 加价）。规格没有单独的货源价时用商品的货源价（在「货源价 → 改价」里可以给每个规格设置）。到手价和商品云库里同一规格的售价对比（没有云库价的用新售价和现价对比），更高时标红显示超出金额；可以逐个修改新售价，勾选要改的规格后应用。</p>
      <el-table v-if="fSpecs.length" :data="fShown" row-key="key" size="small" border max-height="460" :span-method="fSpan">
        <el-table-column width="44" align="center">
          <template #header><el-checkbox :model-value="fAllPicked" :indeterminate="!fAllPicked && fPicked.length > 0" @change="(v: any) => pickAll(!!v)" /></template>
          <template #default="{ row }"><el-checkbox v-if="!row.error" v-model="row.pick" /></template>
        </el-table-column>
        <el-table-column label="商品" min-width="170"><template #default="{ row }"><div class="ellipsis2" :title="row.title">{{ row.title }}</div></template></el-table-column>
        <el-table-column label="规格" min-width="130"><template #default="{ row }"><span class="ellipsis" :title="row.attrs">{{ row.error ? '-' : specName(row.attrs) || '默认' }}</span></template></el-table-column>
        <el-table-column label="货源价" width="75" align="right"><template #default="{ row }">{{ row.error ? '-' : `¥${yuan(row.source)}` }}</template></el-table-column>
        <el-table-column label="优惠券" width="70" align="right"><template #default="{ row }">{{ row.error ? '-' : `¥${yuan(row.coupon)}` }}</template></el-table-column>
        <el-table-column label="云库售价" width="80" align="right"><template #default="{ row }">{{ row.library != null ? `¥${yuan(row.library)}` : '-' }}</template></el-table-column>
        <el-table-column label="现价" width="75" align="right"><template #default="{ row }">{{ row.error ? '-' : `¥${yuan(row.current)}` }}</template></el-table-column>
        <el-table-column label="新售价" width="140" align="center">
          <template #default="{ row }">
            <span v-if="row.error" class="danger">{{ row.error }}</span>
            <el-input-number v-else v-model="row._new" :min="0.01" :precision="2" :step="0.5" size="small" controls-position="right" style="width: 115px" @change="() => recalc(row)" />
          </template>
        </el-table-column>
        <el-table-column label="到手价" width="75" align="right"><template #default="{ row }">{{ row.error ? '-' : `¥${yuan(row.deal)}` }}</template></el-table-column>
        <el-table-column label="成本利润率" width="88" align="right">
          <template #default="{ row }">
            <el-tooltip content="（到手价 − 货源价）÷ 货源价" placement="top"><span :class="rateClass(row)">{{ row.error ? '-' : pct(row.deal - row.source, row.source) }}</span></el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="销售利润率" width="88" align="right">
          <template #default="{ row }">
            <el-tooltip content="（到手价 − 货源价）÷ 到手价" placement="top"><span :class="rateClass(row)">{{ row.error ? '-' : pct(row.deal - row.source, row.deal) }}</span></el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="对比云库售价" width="120" align="center">
          <template #default="{ row }">
            <template v-if="!row.error">
              <div v-if="row.compare_with !== '云库售价'" class="muted small">无云库价，比现价</div>
              <span v-if="row.over > 0" class="danger">超出 ¥{{ yuan(row.over) }}</span>
              <span v-else-if="row.over < 0" class="ok">低 ¥{{ yuan(-row.over) }}</span>
              <span v-else class="muted">不变</span>
            </template>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-if="fPids.length > F_PAGE" v-model:current-page="fPage" :page-size="F_PAGE" :total="fPids.length"
                     layout="total, prev, pager, next" small style="margin-top: 8px" />
      <template #footer>
        <span v-if="fApplying" class="muted" style="margin-right: 12px">{{ fApplyText }}</span>
        <span v-if="fSkipped" class="muted" style="margin-right: 12px">已跳过没有货源的 {{ fSkipped }} 个</span>
        <span v-if="fSpecs.length" class="muted" style="margin-right: 12px">超出云库售价 {{ fSpecs.filter((r) => !r.error && r.over > 0).length }} 个规格</span>
        <el-button v-if="fSpecs.length" @click="pickNotOver">只选不高于云库售价的</el-button>
        <el-button @click="fVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!fPicked.length" :loading="fApplying" @click="applyFormula">应用所选（{{ fPicked.length }} 个规格）</el-button>
      </template>
    </el-dialog>

    <!-- 货源价（每个规格） -->
    <el-dialog v-model="skuSrcVisible" :title="`货源价（每个规格）· ${skuSrcRow?.title || ''}`" width="560px">
      <p class="muted small">货源链接不变。每个规格可以填不同的货源价，没填的规格用最低的那个。{{ skuSrcRow?.source?.from_library ? '这个货源来自商品云库，会一起修改；' : '' }}修改后同步到商品云库和这个商品上架的其他店铺。</p>
      <div v-for="k in skuSrcRows" :key="k.key" class="skurow">
        <span class="ellipsis" :title="k.label">{{ k.label || k.key }}</span>
        <el-input-number v-model="k.price" :min="0.01" :precision="2" :step="0.1" size="small" style="width: 140px" />
      </div>
      <template #footer><el-button @click="skuSrcVisible = false">取消</el-button><el-button type="primary" :loading="skuSrcSaving" @click="saveSkuSrc">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="batchVisible" :title="`批量改价（${selected.length} 个商品，所有规格）`" width="460px">
      <el-radio-group v-model="batch.mode">
        <el-radio value="multiply">按比例</el-radio><el-radio value="add">加减金额</el-radio><el-radio value="set">统一价格</el-radio>
      </el-radio-group>
      <div style="margin-top: 14px">
        <span class="muted">{{ batch.mode === 'multiply' ? '现价 ×' : batch.mode === 'add' ? '现价 +（负数为减）' : '统一设为' }}</span>
        <el-input-number v-model="batch.value" :precision="2" :step="batch.mode === 'multiply' ? 0.05 : 1" style="margin-left: 8px" />
        <span class="muted">{{ batch.mode === 'multiply' ? '倍' : '元' }}</span>
      </div>
      <template #footer><el-button @click="batchVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveBatch">确定</el-button></template>
    </el-dialog>
  </div>
  <ProductPreview ref="previewRef" />
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute } from 'vue-router'
import { http, priceRange, yuan } from '../api'
import ProductPreview from '../components/ProductPreview.vue'
import SourcingStats from '../components/SourcingStats.vue'
const previewRef = ref<any>()

const stores = ref<any[]>([])
const storeId = ref<number | null>(null)
const q = reactive<any>({ keyword: '', status: undefined, source: '', sort: '', page: 1, size: 20 })
const listedDates = ref<string[] | null>(null)  // 上架日期
const items = ref<any[]>([])
const total = ref(0)
const noSourceCount = ref(0)
const draftCount = ref(0)
const unsearchedCount = ref(0)
function showUnsearched() { q.source = 'unsearched'; search() }
const loading = ref(false)
const selected = ref<any[]>([])
const saving = ref(false)

async function loadStores() { stores.value = (await http.get('/products/stores')).data }
function filters() {
  const params: any = { keyword: q.keyword, status: q.status, store_id: storeId.value ?? undefined,
    listed_from: listedDates.value?.[0] || undefined, listed_to: listedDates.value?.[1] || undefined }
  if (params.status === undefined || params.status === '') delete params.status
  return params
}
async function load() {
  loading.value = true
  try {
    const { data } = await http.get('/products', { params: { ...filters(), source: q.source || undefined, sort: q.sort || undefined, page: q.page, size: q.size } })
    items.value = data.items; total.value = data.total; noSourceCount.value = data.no_source_count; draftCount.value = data.draft_count ?? 0; unsearchedCount.value = data.unsearched_count ?? 0
  } finally { loading.value = false }
}
function search() { q.page = 1; load() }
function pick(id: number | null) { storeId.value = id; search() }

async function sync(ids: number[]) {
  await http.post('/products/sync', { store_ids: ids })
  ElMessage.success('已开始同步，商品较多时需要几分钟')
  loadStores()
}

function summarize(res: any[], what: string) {
  const bad = res.filter((r) => !r.ok)
  if (!bad.length) ElMessage.success(`${what}成功 ${res.length} 个`)
  else ElMessageBox.alert(bad.map((b) => `商品 ${items.value.find((i) => i.id === b.id)?.wx_product_id || b.id}：${b.error}`).join('<br>'),
    `${what}：成功 ${res.length - bad.length} 个，失败 ${bad.length} 个`, { dangerouslyUseHTMLString: true })
}

async function act(action: string, rows = selected.value) {
  const name = { listing: '上架', delisting: '下架', delete: '删除' }[action] as string
  if (action === 'delete') await ElMessageBox.confirm(`在微信小店中删除这 ${rows.length} 个商品？在售商品会先下架。删除后无法恢复。`, '确认删除', { type: 'warning' })
  const { data } = await http.post('/products/batch-action', { ids: rows.map((r) => r.id), action })
  summarize(data.results, name); load(); loadStores()
}

const priceVisible = ref(false)
const editing = ref<any>(null)
function openPrice(row: any) {
  editing.value = { ...row, skus: row.skus.map((s: any) => ({ ...s, _new: s.price / 100 })) }
  priceVisible.value = true
}
async function savePrice() {
  const changed = editing.value.skus.filter((s: any) => Math.round(s._new * 100) !== s.price)
  if (!changed.length) return (priceVisible.value = false)
  saving.value = true
  try {
    await http.post(`/products/${editing.value.id}/price`, { skus: changed.map((s: any) => ({ sku_id: s.sku_id, price: s._new })) })
    ElMessage.success('已改价'); priceVisible.value = false; load()
  } finally { saving.value = false }
}

// 库存 (synced to the WeChat store)
const stockVisible = ref(false)
const stockEditing = ref<any>(null)
function openStock(row: any) {
  stockEditing.value = { ...row, skus: row.skus.map((s: any) => ({ ...s, _new: s.stock ?? 0 })) }
  stockVisible.value = true
}
async function saveStock() {
  const changed = stockEditing.value.skus.filter((s: any) => s._new !== s.stock)
  if (!changed.length) return (stockVisible.value = false)
  saving.value = true
  try {
    await http.post(`/products/${stockEditing.value.id}/stock`, { skus: changed.map((s: any) => ({ sku_id: s.sku_id, stock: s._new })) })
    ElMessage.success('库存已同步到微信小店'); stockVisible.value = false; load()
  } finally { saving.value = false; load() }
}
const stockBatchVisible = ref(false)
const stockBatch = reactive({ mode: 'set', value: 100 })
async function saveStockBatch() {
  saving.value = true
  try {
    const { data } = await http.post('/products/batch-stock', { ids: selected.value.map((r) => r.id), ...stockBatch })
    stockBatchVisible.value = false; summarize(data.results, '改库存'); load()
  } finally { saving.value = false }
}

// 货源价 per spec
const skuSrcVisible = ref(false)
const skuSrcSaving = ref(false)
const skuSrcRow = ref<any>(null)
const skuSrcRows = ref<any[]>([])
async function openSkuSrc(row: any, specs: any[]) {
  const prices = row.source.sku_prices || {}
  skuSrcRow.value = row
  skuSrcRows.value = specs.map((k: any) => ({ key: k.key, label: k.label, price: (prices[k.key] ?? row.source.price) / 100 }))
  skuSrcVisible.value = true
}
async function saveSkuSrc() {
  skuSrcSaving.value = true
  try {
    await http.post('/sourcing/sku-prices', { store_product_id: skuSrcRow.value.id,
      prices: Object.fromEntries(skuSrcRows.value.map((k) => [k.key, k.price])) })
    skuSrcVisible.value = false; ElMessage.success('货源价已修改'); load()
  } finally { skuSrcSaving.value = false }
}

// 优惠券 / 公式改价
const couponSyncing = ref(false)
async function syncCoupons() {
  couponSyncing.value = true
  try {
    const { data } = await http.post('/products/coupons/sync', undefined, { params: { store_id: storeId.value } })
    ElMessage.success(`读取到 ${data.count} 张生效中的优惠券`); load()
  } finally { couponSyncing.value = false }
}
const fVisible = ref(false)
const fLoading = ref(false)
const fApplying = ref(false)
const fRows = ref<any[]>([])
const fItems = ref<any[]>([])
// the specs to change: a plain flag per row (selecting thousands of rows one by one froze the page)
const fPicked = computed(() => fSpecs.value.filter((r) => r.pick && !r.error))
const fAllPicked = computed(() => { const ok = fSpecs.value.filter((r) => !r.error); return ok.length > 0 && ok.every((r) => r.pick) })
function pickAll(v: boolean) { for (const r of fSpecs.value) if (!r.error) r.pick = v }
// shown a page of products at a time (a table of every spec of a whole store was too much to draw)
const F_PAGE = 30
const fPage = ref(1)
const fPids = computed(() => [...new Set(fSpecs.value.map((r) => r.pid))])
const fShown = computed(() => { const ids = new Set(fPids.value.slice((fPage.value - 1) * F_PAGE, fPage.value * F_PAGE)); return fSpecs.value.filter((r) => ids.has(r.pid)) })
const fApplyText = ref('')
const ff = reactive({ margin: 2, add: 0 })
// 自动改价 (after 设为货源)
const ap = reactive<any>({ enabled: false, margin: 2, add: 0 })
const apForm = reactive<any>({ enabled: false, margin: 2, add: 0 })
const apVisible = ref(false)
const apSaving = ref(false)
http.get('/products/auto-price').then(({ data }) => { Object.assign(ap, data); Object.assign(apForm, data); ff.margin = data.margin; ff.add = data.add }).catch(() => {})
async function saveAutoPrice() {
  apSaving.value = true
  try {
    const { data } = await http.put('/products/auto-price', { ...apForm })
    Object.assign(ap, data); Object.assign(apForm, data)
    apVisible.value = false
    ElMessage.success(data.enabled ? '已开启自动改价' : '已关闭自动改价')
  } finally { apSaving.value = false }
}
const fSkipped = ref(0)
const fSpecs = ref<any[]>([])  // one row per spec
function openFormula(rows: any[]) { fRows.value = rows; fItems.value = []; fSpecs.value = []; fSkipped.value = 0; fPage.value = 1; fVisible.value = true; preview() }
function recalc(row: any) {
  row.new = Math.round(row._new * 100)
  row.deal = row.new - row.coupon
  row.over = row.compare_with === '云库售价' ? row.deal - row.library : row.new - row.current
}
function fSpan({ row, columnIndex }: any) {  // the product name once for all its specs
  if (columnIndex !== 1) return { rowspan: 1, colspan: 1 }
  return row.first ? { rowspan: row.count, colspan: 1 } : { rowspan: 0, colspan: 0 }
}
async function preview() {
  fLoading.value = true
  try {
    const body: any = { margin: ff.margin, add: ff.add }
    if (fRows.value.length) body.ids = fRows.value.map((r) => r.id)
    else Object.assign(body, filters(), { source: q.source || '' })  // 批量: the whole current list
    const { data } = await http.post('/products/formula-preview', body)
    fSkipped.value = data.skipped_no_source || 0
    fItems.value = data.items
    fSpecs.value = data.items.flatMap((p: any) => p.error
      ? [{ key: `${p.id}`, pid: p.id, title: p.title, error: p.error, first: true, count: 1 }]
      : p.skus.map((k: any, i: number) => ({ ...k, key: `${p.id}:${k.sku_id}`, pid: p.id, title: p.title,
          first: i === 0, count: p.skus.length, _new: k.new / 100, pick: true })))
    fPage.value = 1
  } finally { fLoading.value = false }
}
function pickNotOver() {
  for (const r of fSpecs.value) if (!r.error) r.pick = r.over <= 0
}
async function applyFormula() {
  const over = fPicked.value.filter((r) => r.over > 0)
  if (over.length) await ElMessageBox.confirm(`其中 ${over.length} 个规格的新售价高于云库售价，确定一起修改？`, '提示', { type: 'warning' })
  fApplying.value = true
  try {
    const byProduct = new Map<number, any>()  // the picked specs, per product
    for (const r of fPicked.value) {
      if (!byProduct.has(r.pid)) byProduct.set(r.pid, {})
      byProduct.get(r.pid)[r.sku_id] = r.new
    }
    const all = [...byProduct].map(([id, prices]) => ({ id, prices }))
    const results: any[] = []
    for (let i = 0; i < all.length; i += 20) {  // in batches: each product is one WeChat call
      fApplyText.value = `正在改价 ${i}/${all.length} 个商品…`
      const { data } = await http.post('/products/apply-prices', { items: all.slice(i, i + 20) }, { timeout: 300000 })
      results.push(...data.results)
    }
    fApplyText.value = ''
    fVisible.value = false; summarize(results, '改价'); load()
  } finally { fApplying.value = false }
}
// '颜色:红色；尺码:XL' -> '红色 / XL'
const specName = (attrs: string) => (attrs || '').split('；').map((x) => x.split(':').slice(1).join(':') || x).filter(Boolean).join(' / ')
// 利润率: profit ÷ base, as a percentage with one decimal
const pct = (profit: number, base: number) => (base > 0 ? `${((profit / base) * 100).toFixed(1)}%` : '-')
const rateClass = (row: any) => (row.error ? '' : row.deal - row.source < 0 ? 'danger' : 'ok')
const srcOf = (row: any, skuId: string) => (row.source.sku_prices || {})[skuId] ?? row.source.price

const batchVisible = ref(false)
const batch = reactive({ mode: 'multiply', value: 1.1 })
async function saveBatch() {
  saving.value = true
  try {
    const { data } = await http.post('/products/batch-price', { ids: selected.value.map((r) => r.id), ...batch })
    batchVisible.value = false; summarize(data.results, '改价'); load()
  } finally { saving.value = false }
}

async function editSourcePrice(row: any, key: string) {
  if ((row.skus || []).length > 1)  // several specs: a 货源价 for each
    return openSkuSrc(row, row.skus.map((k: any) => ({ key: k.sku_id, label: k.attrs || k.sku_id })))
  const { value } = await ElMessageBox.prompt(
    `货源链接不变，只修改货源价（元）。${row.source.from_library ? '这个货源是在商品云库中设置的，会一起修改。' : ''}`, '修改货源价',
    { inputValue: yuan(row.source.price), inputPattern: /^(?!0+(\.0+)?$)\d{1,6}(\.\d{1,2})?$/, inputErrorMessage: '请输入大于 0 的价格，最多两位小数' })
  await http.post('/sourcing/price', { [key]: row.id, price: Number(value) })
  ElMessage.success('货源价已修改'); load()
}
async function unlink(row: any) {
  const lib = row.source.from_library
  await ElMessageBox.confirm(lib ? '这个货源是在商品云库中设置的，取消后商品云库中的货源也会一起取消。确定取消？' : '确定取消这个商品的货源？',
    '取消货源', { type: 'warning' })
  await http.post('/sourcing/unlink', { store_product_id: row.id })
  ElMessage.success('已取消货源'); load()
}

// 发布草稿箱: every product in the WeChat 草稿箱 is submitted (上架 → 微信审核 → listed when it passes)
const publishing = ref(false)
const publishText = ref('')
async function publishDrafts() {
  const where = storeId.value ? `「${stores.value.find((s) => s.id === storeId.value)?.name}」` : '所有店铺'
  await ElMessageBox.confirm(
    `把${where}草稿箱里的 ${draftCount.value} 个商品全部提交上架？<br>商品会先提交微信审核，审核通过后自动上架；审核结果在微信小店后台和「同步」后可以看到。`,
    '发布草稿箱', { type: 'info', dangerouslyUseHTMLString: true, confirmButtonText: '全部发布' })
  publishing.value = true
  try {
    const ids: number[] = (await http.get('/products/draft-ids', { params: { store_id: storeId.value ?? undefined, keyword: q.keyword || undefined } })).data
    const results: any[] = []
    for (let i = 0; i < ids.length; i += 20) {
      publishText.value = `发布中 ${Math.min(i, ids.length)}/${ids.length}`
      const { data } = await http.post('/products/batch-action', { ids: ids.slice(i, i + 20), action: 'listing' }, { timeout: 300000 })
      results.push(...data.results)
    }
    if (results.length) summarize(results, '提交上架')
  } finally { publishing.value = false; load() }
}

const purging = ref(false)
async function deleteNoSource() {
  const where = storeId.value ? `「${stores.value.find((s) => s.id === storeId.value)?.name}」中` : '所有店铺中'
  await ElMessageBox.confirm(
    `删除${where}全部 ${noSourceCount.value} 个「无货源」商品（已找过货源但未设为货源）？<br>会同时在微信小店中删除，在售商品会先下架。删除后无法恢复。<br>商品云库中的对应商品会保留，并标记为「无货源」，以后发布时自动跳过，不会重复上架。`,
    '删除全部无货源商品', { type: 'warning', dangerouslyUseHTMLString: true, confirmButtonText: '确认删除' })
  purging.value = true
  try {
    const ids: number[] = (await http.get('/products/no-source-ids', { params: filters() })).data
    const results: any[] = []
    for (let i = 0; i < ids.length; i += 50) {
      const { data } = await http.post('/products/batch-action', { ids: ids.slice(i, i + 50), action: 'delete' })
      results.push(...data.results)
    }
    if (results.length) summarize(results, '删除')
  } finally { purging.value = false; load(); loadStores() }
}

let timer: number | undefined
onMounted(() => {
  const rq = useRoute().query  // from 订单管理: open one product (改价 / 改库存)
  if (rq.keyword) q.keyword = String(rq.keyword)
  if (rq.store_id) storeId.value = Number(rq.store_id)
  loadStores(); load()
  timer = window.setInterval(async () => {
    if (stores.value.some((s) => s.sync_status === 'running')) {
      await loadStores()
      if (!stores.value.some((s) => s.sync_status === 'running')) load()
    }
  }, 3000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.clickable-title { cursor: pointer; }
.clickable-title:hover { color: #409eff; }
.wrap { display: flex; gap: 14px; align-items: flex-start; }
.stores { width: 260px; flex-shrink: 0; }
.main { flex: 1; min-width: 0; }
.store { border: 1px solid #eef0f4; border-radius: 8px; padding: 10px; margin-bottom: 8px; cursor: pointer; }
.store.active { border-color: #409eff; background: #ecf5ff; }
.row { display: flex; align-items: center; gap: 8px; }
.grow { flex: 1; min-width: 0; }
.b { font-weight: 600; }
.small { font-size: 12px; margin-top: 4px; }
.danger { color: #f56c6c; }
.edited { margin-left: 4px; padding: 0 4px; }
.two { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.deal { font-size: 12px; color: #67c23a; }
.libprice { font-size: 12px; color: #909399; }
.ok { color: #67c23a; }
.danger { color: #f56c6c; }
.small { font-size: 12px; }
.skurow { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
.skurow .ellipsis { flex: 1; min-width: 0; }
.skusrc { max-height: 92px; overflow-y: auto; margin: 2px 0; font-size: 12px; text-align: left; }
.skusrc-row { display: flex; justify-content: space-between; gap: 6px; color: #606266; }
.skusrc-row .ellipsis { flex: 1; min-width: 0; color: #909399; }
.skusrc-row .own { color: #e6a23c; }
.ellipsis2 { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
</style>
