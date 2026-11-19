<template>
  <div>
    <div class="card">
      <div class="card-head">
        <div class="card-title">订单管理 <span class="muted">（从微信小店同步，可直接发货）</span></div>
        <el-button type="primary" icon="Refresh" :loading="syncing" @click="sync([])">同步全部店铺订单</el-button>
      </div>
      <div class="stores">
        <div v-for="s in stores" :key="s.id" class="scard" :class="{ active: q.store_id === s.id }"
             @click="q.store_id = q.store_id === s.id ? undefined : s.id; search()">
          <div class="b">{{ s.name }}</div>
          <div class="muted">{{ s.orders }} 个订单</div>
          <div class="small" :class="s.sync_status === 'failed' ? 'danger' : 'muted'">
            <el-icon v-if="s.sync_status === 'running'" class="is-loading"><Loading /></el-icon>
            {{ s.sync_message || '未同步' }}
          </div>
          <el-button link type="primary" size="small" :disabled="s.sync_status === 'running'" @click.stop="sync([s.id])">同步</el-button>
        </div>
      </div>
      <p class="muted small">首次同步读取最近 30 天的订单，之后每次只读取新增和有变化的订单。</p>
    </div>

    <div class="card">
      <el-tabs v-model="statusTab" @tab-change="search">
        <el-tab-pane v-for="t in TABS" :key="t.key" :name="t.key" :label="`${t.label}${t.key !== 'all' && counts[t.key] ? `（${counts[t.key]}）` : ''}`" />
      </el-tabs>
      <el-form :inline="true">
        <el-form-item label="搜索"><el-input v-model="q.keyword" placeholder="订单号 / 收件人 / 电话" clearable style="width: 220px" @keyup.enter="search" /></el-form-item>
        <el-form-item label="下单日期">
          <el-date-picker v-model="dates" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始" end-placeholder="结束" style="width: 240px" />
        </el-form-item>
        <el-form-item><el-button type="primary" icon="Search" @click="search">搜索</el-button></el-form-item>
        <el-form-item>
          <el-button icon="View" :loading="revealingAll" :disabled="!items.some((o) => o.masked)" @click="revealPage">
            {{ revealingAll ? `获取中 ${revealDone}/${revealTotal}` : '显示本页买家信息' }}</el-button>
          <el-button type="success" plain icon="Van" @click="openBatch">批量发货</el-button>
          <el-tooltip v-if="canPrint" content="勾选待发货订单，用电子面单打印快递单（需要电脑上运行微信小店打印组件；只打单，不发货）" placement="top">
            <el-button type="primary" icon="Printer" :disabled="!printable.length" @click="openPrint(printable)">打单（{{ printable.length }}）</el-button>
          </el-tooltip>
          <el-tooltip v-if="canPrint" content="勾选已取号的订单，把面单号退回快递网点（电子面单取消下单），之后可以重新打单" placement="top">
            <el-button type="danger" plain icon="RefreshLeft" :disabled="!cancelable.length" :loading="cancelling" @click="cancelWaybills(cancelable)">回收面单（{{ cancelable.length }}）</el-button>
          </el-tooltip>
          <el-upload :show-file-list="false" accept=".pdf" :http-request="importLabels" :disabled="importing" style="display: inline-block; margin-left: 12px">
            <el-tooltip content="上传从微信小店下载的电子面单 PDF，读取每张面单上的收件人地址，显示在对应订单里" placement="top">
              <el-button icon="Upload" :loading="importing">{{ importing ? '识别中…' : '导入面单 PDF' }}</el-button>
            </el-tooltip>
          </el-upload>
        </el-form-item>
      </el-form>

      <div v-if="summary" class="summary">
        <span>毛利合计 <b :class="summary.margin_total >= 0 ? 'ok' : 'danger'">¥{{ yuan(summary.margin_total) }}</b></span>
        <span class="muted">（{{ summary.orders_with_margin }} 单<template v-if="summary.orders_without_margin">，另有 {{ summary.orders_without_margin }} 单缺货源未计入</template>；销售额 ¥{{ yuan(summary.sales_total) }}；不含待付款和已取消<template v-if="summary.capped">；只统计最新 5000 单</template>）</span>
        <span v-if="summary.negative_count" class="danger">负毛利 {{ summary.negative_count }} 单 ¥{{ yuan(summary.negative_total) }}</span>
        <el-checkbox v-model="onlyNeg" @change="search">只看负毛利订单</el-checkbox>
      </div>
      <el-table :data="items" v-loading="loading" border @selection-change="(v: any[]) => (picked = v)">
        <el-table-column type="selection" width="40" :selectable="(r: any) => r.can_ship" />
        <el-table-column label="订单" width="210">
          <template #default="{ row }">
            <div class="b">{{ row.order_id }}</div>
            <div class="muted small">{{ row.store_name }}</div>
            <div class="muted small">下单 {{ row.create_time }}</div>
            <el-tag :type="statusType(row.status)" size="small">{{ row.status_name }}</el-tag>
            <div v-if="row.purchase_order_no" class="po-box">
              <div class="po-label">1688 采购单号</div>
              <div class="po-no">{{ row.purchase_order_no }}</div>
              <el-button link type="primary" size="small" @click="copyText(row.purchase_order_no, '已复制采购单号')">复制</el-button>
              <el-tooltip :content="row.can_ship ? '打开 1688 订单详情；装了 1688 货源助手插件的话，会读取物流公司和运单号，自动给这个订单发货' : '打开 1688 订单详情'" placement="top">
                <span>
                  <a v-for="(no, i) in poNos(row)" :key="no" class="po-link" target="_blank"
                     :href="`https://trade.1688.com/order/new_step_order_detail.htm?orderId=${no}${row.can_ship ? '#hyship=' + row.id : ''}`">1688查看{{ poNos(row).length > 1 ? i + 1 : '' }}</a>
                </span>
              </el-tooltip>
            </div>
            <el-tag v-else-if="row.can_ship" type="danger" size="small" effect="plain" class="po-none" @click="openPurchase(row)">未采购</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="商品 / 货源" min-width="420">
          <template #default="{ row }">
            <div v-for="(it, i) in row.items" :key="i" class="line">
              <el-image :src="it.thumb" fit="cover" class="thumb clickable" lazy title="预览商品详情" @click="previewRef?.open(row.store_id, it.product_id)" />
              <div class="info">
                <div class="ellipsis" :title="it.title"><span class="clickable" title="预览商品详情" @click="previewRef?.open(row.store_id, it.product_id)">{{ it.title }}</span>
                  <el-link type="primary" :underline="false" class="small" @click="goProduct(row, it)">改售价</el-link></div>
                <div class="muted small">{{ it.attrs || '-' }} · ×{{ it.qty }} · ¥{{ yuan(it.real_price || it.sale_price) }}</div>
                <div v-if="it.product_id" class="muted small">商品ID：<span class="pid">{{ it.product_id }}</span>
                  <el-link type="primary" :underline="false" class="small" title="复制后可在 1688 货源助手里「换商品」" @click="copyText(it.product_id, '已复制商品 ID')">复制</el-link></div>
                <div class="small">
                  <template v-if="it.source">
                    货源：<a :href="it.source.detail_url" target="_blank" :title="it.source.supplier">¥{{ yuan(it.source.price) }} {{ it.source.supplier || '1688' }}</a>
                    <el-tag v-if="it.source.price_edited" size="small" type="warning" effect="plain" class="ml">已改价</el-tag>
                  </template>
                  <span v-else class="warn">未设置货源</span>
                </div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="金额" width="150" align="right">
          <template #default="{ row }">
            <div>实付 <b>¥{{ yuan(row.order_price) }}</b></div>
            <div class="muted small">运费 ¥{{ yuan(row.freight) }}</div>
            <div v-if="row.purchase_cost != null" class="small">采购 ¥{{ yuan(row.purchase_cost) }}</div>
            <div v-else-if="row.source_cost != null" class="muted small">货源 ¥{{ yuan(row.source_cost) }}</div>
            <div v-if="row.margin != null" class="small" :class="row.margin >= 0 ? 'ok' : 'danger'">
              毛利 ¥{{ yuan(row.margin) }}<span v-if="!row.margin_real" class="muted">（预估）</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="收件人" min-width="220">
          <template #default="{ row }">
            <div>{{ row.receiver_name }} <span :class="{ muted: row.phone_masked }">{{ row.receiver_phone }}</span>
              <el-link type="primary" :underline="false" class="small ml" @click="openMsg(row)">发消息</el-link></div>
            <div v-if="row.virtual_phone" class="small">
              <el-tooltip content="买家使用了号码保护：请拨打这个虚拟号（转分机号）联系买家，填写快递单也用它" placement="top">
                <el-tag size="small" type="warning" effect="plain">虚拟号</el-tag>
              </el-tooltip>
              <b>{{ row.virtual_phone }}</b>
              <span v-if="row.virtual_phone_expire" class="muted">（{{ row.virtual_phone_expire.slice(0, 10) }} 到期）</span>
            </div>
            <div class="muted small">{{ row.receiver_address }}</div>
            <el-button v-if="row.masked" link type="primary" size="small" :loading="row._revealing" @click="reveal(row)">显示买家信息</el-button>
            <template v-else-if="row.receiver_name">
              <el-button link type="primary" size="small" @click="copyBuyer(row)">复制</el-button>
              <el-button v-if="row.phone_masked && !row.virtual_phone" link type="primary" size="small" :loading="row._revealing" @click="reveal(row, true)">重新获取</el-button>
              <el-button v-if="row.virtual_phone" link type="primary" size="small" @click="delayVirtual(row)">虚拟号延期</el-button>
              <el-button v-if="row.phone_masked" link type="primary" size="small" @click="applyReal(row)">申请真实号码</el-button>
              <el-button v-if="row._applied" link size="small" @click="checkReal(row)">查询审核</el-button>
            </template>
            <div v-if="row.label_address" class="label-addr small">
              <el-tooltip content="从导入的面单 PDF 读取：姓名只有姓氏；电话后 4 位是面单上的，中间 6 位是随机生成的（不是买家真实号码）" placement="top">
                <el-tag size="small" type="success" effect="plain">面单地址</el-tag>
              </el-tooltip>
              {{ row.label_address.name }} {{ row.label_address.phone }}
              <div>{{ row.label_address.address }}</div>
              <el-button link type="primary" size="small" @click="copyLabel(row)">复制面单地址</el-button>
            </div>
            <div v-if="row.buyer_note" class="warn small">买家留言：{{ row.buyer_note }}</div>
          </template>
        </el-table-column>
        <el-table-column label="物流" width="190">
          <template #default="{ row }">
            <div v-for="(d, i) in row.deliveries" :key="i" class="small">
              {{ companyName(d.delivery_id) }} <a :href="`https://www.kuaidi100.com/chaxun?nu=${d.waybill_id}`" target="_blank">{{ d.waybill_id }}</a>
              <div class="muted">{{ d.time }}</div>
            </div>
            <span v-if="!row.deliveries.length" class="muted small">未发货</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="124" align="center">
          <template #default="{ row }">
            <div><el-button v-if="row.can_ship" type="primary" size="small" @click="openShip(row)">发货</el-button></div>
            <div><el-button v-if="row.can_ship && canPrint" link type="primary" size="small" @click="openPrint([row])">打单</el-button></div>
            <div><el-button v-if="row.ewaybill?.waybill_id && canPrint" link type="danger" size="small" @click="cancelWaybills([row])">回收面单</el-button></div>
            <div><el-button v-if="row.status === 20" link type="primary" size="small" @click="openNego(row)">协商或取消</el-button></div>
            <div><el-button link type="warning" size="small" @click="openPurchase(row)">{{ row.purchased_at ? '采购✓' : '采购' }}</el-button></div>
            <div><el-button v-if="row.deliveries.length" link type="primary" size="small" @click="openShip(row, true)">补发货</el-button></div>
            <div><el-button link type="primary" size="small" @click="showRaw(row)">原始数据</el-button></div>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <el-pagination v-model:current-page="q.page" v-model:page-size="q.size" :total="total" :page-sizes="[20, 50, 100]"
                       layout="total, sizes, prev, pager, next" @change="load" />
      </div>
      <p class="muted small">毛利 = 实付 − 货源价 × 数量（不含采购运费），只在每个商品都设置了货源时显示。</p>
      <p class="muted small">微信同步的订单中，买家姓名、电话、地址是打码的（如 郑**）。点「显示买家信息」通过微信接口获取完整信息并保存在订单里，之后不会重复获取（该接口每天有调用次数限制）。买家信息仅用于发货，请勿外传。</p>
    </div>

    <!-- 发货 / 补发货 -->
    <el-dialog v-model="shipVisible" :title="`${sf.compensation ? '补发货' : '发货'} · ${shipRow?.order_id || ''}`" width="520px">
      <el-form label-width="90px">
        <el-form-item label="发货方式">
          <el-radio-group v-model="sf.deliver_type"><el-radio :value="1">快递</el-radio><el-radio :value="3">无需物流（虚拟商品）</el-radio></el-radio-group>
        </el-form-item>
        <template v-if="sf.deliver_type === 1">
          <el-form-item label="快递公司" required>
            <el-select v-model="sf.delivery_id" filterable placeholder="选择或搜索快递公司" style="width: 100%">
              <el-option v-for="c in companies" :key="c.id" :label="`${c.name}（${c.id}）`" :value="c.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="运单号" required><el-input v-model="sf.waybill_id" placeholder="快递单号" /></el-form-item>
        </template>
        <el-form-item v-if="sf.compensation" label="补发原因"><el-input v-model="sf.reason" placeholder="如：漏发、拆分包裹、坏损补发、赠品" /></el-form-item>
        <el-form-item label="商品">
          <div class="muted small">{{ sf.compensation ? '补发订单内全部商品（每单最多补发 10 次，不计入发货考核）' : '发送订单中所有还没发货的商品' }}</div>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="shipVisible = false">取消</el-button><el-button type="primary" :loading="shipping" @click="doShip">确认{{ sf.compensation ? '补发' : '发货' }}</el-button></template>
    </el-dialog>

    <!-- 采购 -->
    <el-dialog v-model="purVisible" :title="`采购 · ${purRow?.order_id || ''}`" width="600px">
      <el-form label-width="110px">
        <el-form-item label="采购单号"><el-input v-model="pf.purchase_order_no" placeholder="如 1688 订单号" clearable /></el-form-item>
        <el-form-item label="采购实付（元）">
          <el-input-number v-model="pf.purchase_cost" :min="0" :precision="2" :step="1" placeholder="含采购运费" style="width: 180px" />
          <span class="muted small ml">含运费、扣除优惠后实际付的钱；填写后毛利按实际计算</span>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="pf.purchase_note" placeholder="可选" /></el-form-item>
        <el-form-item v-if="pf.lines.length" label="货源单价（元）">
          <div style="width: 100%">
            <div v-for="l in pf.lines" :key="l.source_id" class="pline">
              <span class="ellipsis" :title="l.title">{{ l.title }}</span>
              <el-input-number v-model="l.unit_price" :min="0.01" :precision="2" :step="0.1" size="small" style="width: 130px" />
            </div>
            <div class="muted small">改了单价会同时更新这个商品的货源价（保留 1688 原价），以后的订单按新价格预估毛利。</div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="purVisible = false">取消</el-button><el-button type="primary" :loading="purSaving" @click="savePurchase">保存</el-button></template>
    </el-dialog>

    <!-- 批量发货 -->
    <el-dialog v-model="batchVisible" title="批量发货" width="640px">
      <p class="muted small">每行一个订单：<b>订单号　快递公司　运单号</b>（空格、Tab 或逗号分隔，可以直接从 Excel 复制三列粘贴）。快递公司可以写名称（如 中通）或编码（如 ZTO）。</p>
      <el-input v-model="batchText" type="textarea" :rows="10" placeholder="3740615526570443264 中通 78812345678901" />
      <el-table v-if="batchResult" :data="batchResult.results.filter((r: any) => !r.ok)" size="small" max-height="220" style="margin-top: 8px">
        <el-table-column prop="line" label="行" show-overflow-tooltip />
        <el-table-column prop="error" label="失败原因" show-overflow-tooltip />
      </el-table>
      <template #footer>
        <span v-if="batchResult" class="muted" style="margin-right: 12px">成功 {{ batchResult.ok }} 个，失败 {{ batchResult.failed }} 个</span>
        <el-button @click="batchVisible = false">关闭</el-button>
        <el-button type="primary" :loading="batching" @click="doBatch">发货</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="rawVisible" :title="`订单原始数据 ${rawRow?.order_id || ''}`" width="760px">
      <el-button size="small" @click="copyRaw">复制</el-button>
      <pre class="raw">{{ rawText }}</pre>
    </el-dialog>
    <!-- 打单发货: 电子面单 -->
    <el-dialog v-model="printVisible" :title="`打单发货（${printRows.length} 个订单）`" width="640px">
      <el-alert v-if="printErr" type="error" :closable="false" show-icon :title="printErr" style="margin-bottom: 10px" />
      <el-form label-width="100px" v-loading="printLoading">
        <el-form-item label="面单账号">
          <el-select v-model="pform.acct" value-key="acct_id" placeholder="选择快递公司 / 网点" style="width: 100%" @change="onAcct">
            <el-option v-for="a in printOpts.accounts" :key="a.acct_id" :value="a" :label="`${companyName(a.delivery_id)} · ${a.site_name || a.monthly_card || a.acct_id}`" />
          </el-select>
          <div v-if="!printOpts.accounts.length && !printLoading" class="muted small">没有可用的面单账号：请先在微信小店后台「订单 → 电子面单」开通并绑定快递网点</div>
        </el-form-item>
        <el-form-item label="面单模板">
          <el-select v-model="pform.template_id" style="width: 100%">
            <el-option label="默认模板" value="single" />
            <el-option v-for="t in (printOpts.templates[pform.acct?.delivery_id]?.list || [])" :key="t.template_id" :value="t.template_id" :label="t.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="寄件人">
          <div class="sender">
            <el-input v-model="pform.sender.name" placeholder="姓名" style="width: 110px" />
            <el-input v-model="pform.sender.mobile" placeholder="手机" style="width: 140px" />
            <el-input v-model="pform.sender.province" placeholder="省" style="width: 90px" />
            <el-input v-model="pform.sender.city" placeholder="市" style="width: 90px" />
            <el-input v-model="pform.sender.county" placeholder="区县" style="width: 90px" />
            <el-input v-model="pform.sender.address" placeholder="详细地址" />
          </div>
        </el-form-item>
        <el-form-item label="打印机">
          <el-select v-model="pform.printer" placeholder="默认打印机" clearable style="width: 300px">
            <el-option v-for="p in printers" :key="p.name" :value="p.name" :label="p.displayName || p.name" />
          </el-select>
          <el-button link type="primary" style="margin-left: 8px" @click="loadPrinters">刷新</el-button>
          <div v-if="printerErr" class="warn small">{{ printerErr }}（在微信小店后台「打单发货」页面可以下载打印组件）</div>
        </el-form-item>
        <el-form-item><span class="muted small">只打印快递单，不会发货。打印后会自动读取面单上的收件地址，显示在订单里。</span></el-form-item>
      </el-form>
      <div v-if="printStep" class="muted small">{{ printStep }}</div>
      <!-- after printing: read the receiver from the saved label PDF -->
      <div v-if="needPdf" class="pdf-drop">
        <p class="small">已打印 <b>{{ needPdf }}</b> 张面单。打印到 PDF 的话，<b>保存好后把 PDF 拖到下面</b>，识别收件人地址并显示在订单里（只识别收件人，不识别快递单号）。</p>
        <el-upload drag :show-file-list="false" accept=".pdf" :http-request="importAfterPrint" :disabled="importing">
          <div class="small">{{ importing ? '识别中…' : '把保存的面单 PDF 拖到这里，或点击选择' }}</div>
        </el-upload>
      </div>
      <template #footer>
        <el-button @click="printVisible = false">{{ needPdf ? '完成' : '取消' }}</el-button>
        <el-button v-if="!needPdf" type="primary" :loading="printing" :disabled="!pform.acct" @click="doPrint">打印快递单</el-button>
      </template>
    </el-dialog>
    <!-- 发货协商/承诺/取消 (like 微信小店后台) -->
    <el-dialog v-model="negoVisible" width="720px" class="nego-dlg">
      <template #header>
        <div class="nego-title">发货协商/承诺/取消</div>
        <div class="muted small">可向买家推送通知（有效期4小时），经买家同意后可调整发货时间或取消订单。</div>
      </template>
      <el-form label-width="96px" label-position="left" v-loading="negoLoading">
        <el-form-item label="工具类型">
          <el-radio-group v-model="nego.tool">
            <el-radio value="time">修改发货时间</el-radio>
            <el-radio value="arrive">约定送达时间</el-radio>
            <el-radio value="today">承诺今日发货</el-radio>
            <el-radio value="cancel">取消订单并补偿</el-radio>
          </el-radio-group>
          <div class="muted small" style="width: 100%">{{ TOOL_TIPS[nego.tool] }}</div>
        </el-form-item>
        <template v-if="nego.tool === 'time'">
          <el-form-item label="适用原因">
            <el-select v-model="nego.reason" placeholder="请选择适用原因" style="width: 100%">
              <el-option v-for="r in negoInfo.reasons" :key="r.value" :label="r.label" :value="r.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="受影响订单">
            <div>
              <div>{{ negoRow?.order_id }}</div>
              <div v-if="negoInfo.delivery_deadline" class="muted small">当前应发货时间 {{ negoInfo.delivery_deadline }}</div>
            </div>
          </el-form-item>
          <el-form-item label="新发货时间">
            <el-date-picker v-model="nego.time" type="datetime" format="YYYY-MM-DD HH:mm" value-format="YYYY-MM-DD HH:mm" placeholder="选择预计发货时间" :disabled-date="(d: Date) => d.getTime() < Date.now() - 86400000" />
          </el-form-item>
          <el-form-item label="备注"><el-input v-model="nego.remark" maxlength="200" placeholder="可选" /></el-form-item>
          <div v-if="negoInfo.error" class="warn small">{{ negoInfo.error }}</div>
          <div v-if="negoInfo.items.length" class="small">
            <div class="b">协商记录</div>
            <div v-for="(n, i) in negoInfo.items" :key="i" class="nego-line">
              {{ n.create_time }}：{{ n.original_delivery_time }} → <b>{{ n.predict_delivery_time }}</b>（{{ n.reason }}）
              <el-tag size="small" :type="n.state === 2 ? 'success' : n.state === 1 ? 'warning' : 'info'">{{ n.state_name }}</el-tag>
            </div>
          </div>
        </template>
        <template v-else>
          <el-form-item label="受影响订单">{{ negoRow?.order_id }}</el-form-item>
          <el-alert type="info" :closable="false" show-icon
            :title="`微信没有开放「${TOOL_NAMES[nego.tool]}」的接口，网站无法代为提交，请到微信小店后台 → 订单管理 → 该订单「协商或取消」里操作。`" />
          <div style="margin-top: 10px"><el-button size="small" @click="copyText(negoRow?.order_id, '已复制订单号')">复制订单号</el-button>
            <a href="https://store.weixin.qq.com/" target="_blank" class="ml small">打开微信小店后台</a></div>
          <div v-if="nego.tool === 'cancel'" class="refund-box">
            <div class="b">或者：直接退款取消（不需要买家同意）</div>
            <div class="muted small">代买家发起「仅退款」售后，按实付全额 ¥{{ yuan(negoRow?.order_price) }} 退款，退款完成后订单变为「售后取消」。没有补偿金。</div>
            <div style="display: flex; gap: 8px; margin-top: 6px">
              <el-select v-model="nego.cancelReason" size="small" style="width: 180px">
                <el-option v-for="(label, v) in CANCEL_REASONS" :key="v" :label="label" :value="v" />
              </el-select>
              <el-input v-model="nego.desc" size="small" maxlength="200" placeholder="补充说明（可选）" />
              <el-button size="small" type="danger" plain :loading="negoSaving" @click="submitCancel">退款取消</el-button>
            </div>
          </div>
        </template>
      </el-form>
      <template #footer>
        <div class="nego-foot">
          <span class="muted small">剩余次数 {{ negoInfo.left }}/3</span>
          <span>
            <el-button @click="negoVisible = false">取消</el-button>
            <el-button type="primary" :loading="negoSaving" :disabled="nego.tool !== 'time' || !negoInfo.left || !nego.time || !nego.reason" @click="submitNego">提交</el-button>
          </span>
        </div>
      </template>
    </el-dialog>

    <!-- 发消息 -->
    <el-dialog v-model="msgVisible" :title="`给买家发消息 · ${msgRow?.receiver_name || ''}`" width="520px">
      <div v-loading="msgLoading">
        <div v-if="msgInfo.items?.length" class="chat">
          <div v-for="m in msgInfo.items" :key="m.id" :class="['bubble', m.direction]">
            <div>{{ m.content }}</div><div class="muted small">{{ m.sender ? m.sender + ' · ' : '' }}{{ m.created_at }}</div>
          </div>
        </div>
        <p class="muted small">通过微信「自研客服 API」发送（店铺需在小店后台 → 客服管理 → 客服设置里「接入自研客服」）：买家发消息后 48 小时内可以回复，最多 5 条。
          <template v-if="msgInfo.can_reply">还可以发 {{ msgInfo.left }} 条（{{ msgInfo.deadline }} 前）。</template>
          <span v-else-if="msgInfo.reason" class="warn">{{ msgInfo.reason }}（也可以直接试试，以微信的结果为准）</span></p>
        <el-input v-model="msgText" type="textarea" :rows="4" maxlength="1000" show-word-limit placeholder="填写内容，如：您好，您的订单已加急处理，预计明天发货" />
        <el-checkbox v-model="msgCard" style="margin-top: 6px">同时发送订单卡片</el-checkbox>
      </div>
      <template #footer>
        <el-button @click="msgVisible = false">关闭</el-button>
        <el-button type="primary" :loading="msgSending" :disabled="!msgText.trim() && !msgCard" @click="sendMsg">发送</el-button>
      </template>
    </el-dialog>
  </div>
  <ProductPreview ref="previewRef" />
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { http, yuan } from '../api'
import ProductPreview from '../components/ProductPreview.vue'
import { previewWaybill, printerList, printWaybills } from '../utils/printer'
const previewRef = ref<any>()

const TABS = [
  { key: 'all', label: '全部' }, { key: '20', label: '待发货' }, { key: '21', label: '部分发货' },
  { key: '30', label: '待收货' }, { key: '100', label: '已完成' }, { key: '10', label: '待付款' }, { key: '250', label: '已取消' },
]
const statusType = (s: number) => (s === 20 || s === 21 ? 'danger' : s === 30 ? 'warning' : s === 100 ? 'success' : 'info')

const stores = ref<any[]>([])
const items = ref<any[]>([])
const summary = ref<any>(null)
const onlyNeg = ref(false)
const total = ref(0)
const counts = ref<Record<string, number>>({})
const loading = ref(false)
const statusTab = ref('all')
const dates = ref<string[] | null>(null)
const q = reactive({ page: 1, size: 20, store_id: undefined as number | undefined, keyword: '' })

async function loadStores() { stores.value = (await http.get('/orders/stores')).data }
async function load() {
  loading.value = true
  try {
    const { data } = await http.get('/orders', { params: {
      page: q.page, size: q.size, store_id: q.store_id, keyword: q.keyword || undefined,
      status: statusTab.value === 'all' ? undefined : Number(statusTab.value),
      date_from: dates.value?.[0], date_to: dates.value?.[1], margin: onlyNeg.value ? 'neg' : undefined } })
    items.value = data.items; total.value = data.total; counts.value = data.status_counts; summary.value = data.summary
  } finally { loading.value = false }
}
function search() { q.page = 1; load() }
const poNos = (row: any) => String(row.purchase_order_no || '').split(/[,，\s]+/).filter((n) => /^\d{10,}$/.test(n))

// ---- 协商或取消: 修改发货时间 / 取消订单（代买家仅退款） ----
const TOOL_NAMES: Record<string, string> = { time: '修改发货时间', arrive: '约定送达时间', today: '承诺今日发货', cancel: '取消订单并补偿' }
const TOOL_TIPS: Record<string, string> = { time: '将按照发货时间进行发货考核。', arrive: '与买家约定送达时间，按约定时间考核。', today: '承诺当日发货，买家可见。', cancel: '用户同意并领取补偿后，订单即可取消。' }
const CANCEL_REASONS: Record<string, string> = { '10000014': '双方协商一致退款', '10000001': '缺货', '10000002': '拍错/多拍', '10000000': '不想要了', '10000008': '其他' }
const negoVisible = ref(false)
const negoLoading = ref(false)
const negoSaving = ref(false)
const negoRow = ref<any>(null)
const negoInfo = ref<any>({ items: [], left: 3, reasons: [] })
const nego = reactive<any>({ tool: 'time', time: '', reason: undefined, remark: '', cancelReason: '10000014', desc: '' })
async function openNego(row: any) {
  negoRow.value = row
  Object.assign(nego, { tool: 'time', time: '', reason: undefined, remark: '', cancelReason: '10000014', desc: '' })
  negoInfo.value = { items: [], left: 3, reasons: [{ value: 1, label: '买家要求' }, { value: 2, label: '商品库存不足' }, { value: 3, label: '缺货保障' }, { value: 4, label: '收货信息有误' }] }
  negoVisible.value = true; negoLoading.value = true
  try { negoInfo.value = (await http.get(`/orders/${row.id}/negotiation`, { headers: { 'X-Silent': '1' } })).data } catch { /* keep the defaults */ } finally { negoLoading.value = false }
}
async function submitNego() {
  negoSaving.value = true
  try {
    negoInfo.value = (await http.post(`/orders/${negoRow.value.id}/negotiate`, { predict_delivery_time: nego.time, reason: nego.reason, remark: nego.remark })).data
    ElMessage.success('已向买家发送协商，买家同意后生效')
  } finally { negoSaving.value = false }
}
async function submitCancel() {
  await ElMessageBox.confirm(`确定取消订单 ${negoRow.value.order_id}，全额退款 ¥${yuan(negoRow.value.order_price)} 给买家？`, '取消订单', { type: 'warning', confirmButtonText: '取消订单并退款', cancelButtonText: '再想想' })
  negoSaving.value = true
  try {
    const { data } = await http.post(`/orders/${negoRow.value.id}/cancel`, { reason: nego.cancelReason, desc: nego.desc })
    const bad = data.results.filter((r: any) => !r.ok)
    if (bad.length) ElMessageBox.alert(bad.map((b: any) => `${b.title}：${b.error}`).join('<br>'), '部分商品退款失败', { dangerouslyUseHTMLString: true })
    else { ElMessage.success('已发起退款，退款完成后订单会变为「售后取消」'); negoVisible.value = false }
    Object.assign(negoRow.value, data.order)
  } finally { negoSaving.value = false }
}

// ---- 发消息 (商家客服) ----
const msgVisible = ref(false)
const msgLoading = ref(false)
const msgSending = ref(false)
const msgRow = ref<any>(null)
const msgInfo = ref<any>({ items: [] })
const msgText = ref('')
const msgCard = ref(false)
async function openMsg(row: any) {
  msgRow.value = row; msgText.value = ''; msgCard.value = false; msgInfo.value = { items: [] }
  msgVisible.value = true; msgLoading.value = true
  try { msgInfo.value = (await http.get(`/orders/${row.id}/message`)).data } finally { msgLoading.value = false }
}
async function sendMsg() {
  msgSending.value = true
  try {
    msgInfo.value = (await http.post(`/orders/${msgRow.value.id}/message`, { text: msgText.value, order_card: msgCard.value })).data
    msgText.value = ''; msgCard.value = false
    ElMessage.success('已发送')
  } finally { msgSending.value = false }
}

const syncing = ref(false)
let timer: number | undefined
async function sync(ids: number[]) {
  syncing.value = true
  try {
    const { data } = await http.post('/orders/sync', { store_ids: ids })
    ElMessage.success(data.started.length ? `已开始同步 ${data.started.length} 个店铺的订单` : '正在同步中')
    await loadStores(); poll()
  } finally { syncing.value = false }
}
function poll() {
  clearInterval(timer)
  timer = window.setInterval(async () => {
    await loadStores()
    if (!stores.value.some((s) => s.sync_status === 'running')) { clearInterval(timer); load() }
  }, 3000)
}

// 买家信息: decoded once per order through the WeChat API, then kept on the order
async function reveal(row: any, again = false) {
  row._revealing = true
  try { Object.assign(row, (await http.post(`/orders/${row.id}/reveal`, undefined, { params: again ? { again: true } : {} })).data) } finally { row._revealing = false }
}
async function delayVirtual(row: any) {
  Object.assign(row, (await http.post(`/orders/${row.id}/virtual-delay`)).data)
  ElMessage.success(`虚拟号已延期${row.virtual_phone_expire ? `，${row.virtual_phone_expire.slice(0, 10)} 到期` : ''}`)
}
async function applyReal(row: any) {
  await ElMessageBox.confirm('向微信申请查看这个订单买家的真实手机号？需要微信审核，通过后点「查询审核」即可显示真实号码。', '申请真实号码', { type: 'info' })
  await http.post(`/orders/${row.id}/real-number`)
  row._applied = true
  ElMessage.success('已提交申请，审核通过后点「查询审核」')
}
async function checkReal(row: any) {
  const { data } = await http.get(`/orders/${row.id}/real-number`)
  Object.assign(row, data.order)
  if (!row.phone_masked) ElMessage.success('已显示真实号码')
  else ElMessageBox.alert(`<pre style="white-space: pre-wrap">${JSON.stringify(data.response, null, 2)}</pre>`, '审核状态', { dangerouslyUseHTMLString: true })
}

// 发货
const companies = ref<any[]>([])
async function loadCompanies() { if (!companies.value.length) companies.value = (await http.get('/orders/delivery-companies')).data }
const companyName = (id: string) => companies.value.find((c) => c.id === id)?.name || id
const shipVisible = ref(false)
const shipping = ref(false)
const shipRow = ref<any>(null)
const sf = reactive<any>({ delivery_id: '', waybill_id: '', deliver_type: 1, compensation: false, reason: '' })
async function openShip(row: any, compensation = false) {
  shipRow.value = row
  Object.assign(sf, { delivery_id: sf.delivery_id, waybill_id: '', deliver_type: 1, compensation, reason: '' })
  shipVisible.value = true
  await loadCompanies()
}
async function doShip() {
  shipping.value = true
  try {
    const { data } = await http.post(`/orders/${shipRow.value.id}/ship`, sf)
    Object.assign(shipRow.value, data)
    shipVisible.value = false
    ElMessage.success(sf.compensation ? '已补发货' : '已发货')
  } finally { shipping.value = false }
}
// 采购
const purVisible = ref(false)
const purSaving = ref(false)
const purRow = ref<any>(null)
const pf = reactive<any>({ purchase_order_no: '', purchase_cost: undefined, purchase_note: '', lines: [] })
function openPurchase(row: any) {
  purRow.value = row
  const seen = new Set()  // one line per ordered spec (its 货源价)
  const lines = row.items.filter((it: any) => it.source && !seen.has(`${it.product_id}:${it.sku_id}`) && seen.add(`${it.product_id}:${it.sku_id}`))
    .map((it: any) => ({ source_id: it.source.id, product_id: it.product_id, sku_id: it.sku_id,
      title: it.attrs ? `${it.title}（${it.attrs}）` : it.title, unit_price: it.source.price / 100, old: it.source.price }))
  Object.assign(pf, { purchase_order_no: row.purchase_order_no, purchase_note: row.purchase_note, lines,
    purchase_cost: row.purchase_cost != null ? row.purchase_cost / 100 : undefined })
  purVisible.value = true
}
async function savePurchase() {
  purSaving.value = true
  try {
    const { data } = await http.put(`/orders/${purRow.value.id}/purchase`, {
      purchase_order_no: pf.purchase_order_no || '', purchase_note: pf.purchase_note || '',
      purchase_cost: pf.purchase_cost ?? null,
      source_prices: pf.lines.filter((l: any) => Math.round(l.unit_price * 100) !== l.old).map((l: any) => ({
        source_id: l.source_id, unit_price: l.unit_price, product_id: l.product_id, sku_id: l.sku_id })) })
    Object.assign(purRow.value, data)
    purVisible.value = false
    ElMessage.success('已保存')
    if (pf.lines.some((l: any) => Math.round(l.unit_price * 100) !== l.old)) load()  // other orders of the product
  } finally { purSaving.value = false }
}
const router = useRouter()
function goProduct(row: any, it: any) { router.push({ path: '/products', query: { keyword: it.product_id, store_id: row.store_id } }) }

const batchVisible = ref(false)
const batchText = ref('')
const batching = ref(false)
const batchResult = ref<any>(null)
function openBatch() { batchResult.value = null; batchVisible.value = true; loadCompanies() }
async function doBatch() {
  if (!batchText.value.trim()) return ElMessage.warning('请粘贴要发货的订单')
  batching.value = true
  try {
    batchResult.value = (await http.post('/orders/ship-batch', { text: batchText.value, store_id: q.store_id })).data
    if (!batchResult.value.failed) { ElMessage.success(`已发货 ${batchResult.value.ok} 个订单`); batchVisible.value = false }
    load()
  } finally { batching.value = false }
}
const revealingAll = ref(false)
const revealDone = ref(0)
const revealTotal = ref(0)
async function revealPage() {
  const rows = items.value.filter((o) => o.masked)
  revealingAll.value = true; revealDone.value = 0; revealTotal.value = rows.length
  let failed = 0
  try {
    for (const row of rows) {
      try { Object.assign(row, (await http.post(`/orders/${row.id}/reveal`)).data) } catch { failed++ }
      revealDone.value++
    }
  } finally { revealingAll.value = false }
  failed ? ElMessage.warning(`${failed} 个订单获取失败，可单独点「显示买家信息」查看原因`) : ElMessage.success(`已显示 ${rows.length} 个订单的买家信息`)
}
async function copyBuyer(row: any) {
  const phone = row.virtual_phone && row.phone_masked ? row.virtual_phone : row.receiver_phone
  const text = `${row.receiver_name} ${phone} ${row.receiver_address}`
  try { await navigator.clipboard.writeText(text); ElMessage.success('已复制收件信息') } catch { ElMessage.warning('复制失败，请手动选择文字复制') }
}

const rawVisible = ref(false)
const rawRow = ref<any>(null)
const rawText = ref('')
async function showRaw(row: any) {
  rawRow.value = row; rawText.value = '加载中…'; rawVisible.value = true
  const { data } = await http.get(`/orders/${row.id}`)
  rawText.value = JSON.stringify(data.decoded ? { 订单详情: data.raw, 解码买家信息: data.decoded } : data.raw, null, 2)
}
async function copyRaw() {
  try { await navigator.clipboard.writeText(rawText.value); ElMessage.success('已复制') } catch { ElMessage.warning('复制失败，请手动选择文字复制') }
}

onMounted(async () => { await loadStores(); load(); loadCompanies().catch(() => {}); if (stores.value.some((s) => s.sync_status === 'running')) poll() })
onUnmounted(() => clearInterval(timer))

// ---- 打单发货 (电子面单): 取号 on the server → 打印组件 on this PC prints → 打印通知 + 发货 ----
const picked = ref<any[]>([])
const canPrint = ref(false)
http.get('/auth/me').then(({ data }) => { canPrint.value = !!data.can_print }).catch(() => {})
const printable = computed(() => picked.value.filter((r) => r.can_ship))
const cancelable = computed(() => picked.value.filter((r) => r.ewaybill?.waybill_id))
const cancelling = ref(false)
async function cancelWaybills(rows: any[]) {
  const nums = rows.map((r) => r.ewaybill.waybill_id)
  await ElMessageBox.confirm(`回收 ${rows.length} 张面单（${nums.slice(0, 3).join('、')}${nums.length > 3 ? ' 等' : ''}）？面单号将退回快递网点，已贴在包裹上的面单不能再用；回收后可以重新打单。`,
    '回收面单', { type: 'warning', confirmButtonText: '回收' })
  cancelling.value = true
  try {
    const { data } = await http.post('/orders/print/cancel', { order_ids: rows.map((r) => r.id) })
    const bad = data.results.filter((r: any) => !r.ok)
    const ok = data.results.length - bad.length
    if (!bad.length) ElMessage.success(`已回收 ${ok} 张面单`)
    else ElMessageBox.alert([`成功 ${ok} 张，失败 ${bad.length} 张：`, ...bad.map((b: any) => `${b.order_id || b.id}：${b.error}`)].join('<br>'),
      '回收面单', { dangerouslyUseHTMLString: true })
    load()
  } finally { cancelling.value = false }
}
const printVisible = ref(false)
const printLoading = ref(false)
const printing = ref(false)
const printErr = ref('')
const printStep = ref('')
const needPdf = ref(0)  // printed, the receiver not read yet: drop the saved PDF
const printRows = ref<any[]>([])
const printOpts = ref<any>({ accounts: [], templates: {}, sender: {} })
const printers = ref<any[]>([])
const printerErr = ref('')
const pform = reactive<any>({ acct: null, template_id: 'single', printer: '', ship: false,
  sender: { name: '', mobile: '', province: '', city: '', county: '', street: '', address: '' } })
async function loadPrinters() {
  printerErr.value = ''
  try { printers.value = await printerList() } catch (e: any) { printers.value = []; printerErr.value = e.message }
}
async function openPrint(rows: any[]) {
  const stores = [...new Set(rows.map((r) => r.store_id))]
  if (stores.length > 1) return ElMessage.warning('一次只能给同一个店铺的订单打单，请按店铺分开勾选')
  printRows.value = rows; printErr.value = ''; printStep.value = ''; needPdf.value = 0; printVisible.value = true; printLoading.value = true
  loadPrinters()
  try {
    printOpts.value = (await http.get('/orders/print/options', { params: { store_id: stores[0] } })).data
    if (printOpts.value.errors?.length) printErr.value = printOpts.value.errors.join('；')
    Object.assign(pform.sender, printOpts.value.sender || {})
    if (!pform.acct || !printOpts.value.accounts.some((a: any) => a.acct_id === pform.acct.acct_id)) { pform.acct = printOpts.value.accounts[0] || null; onAcct() }
  } finally { printLoading.value = false }
}
function onAcct() {
  const a = pform.acct
  if (!a) return
  pform.template_id = printOpts.value.templates[a.delivery_id]?.default || 'single'
  const sa = a.sender_address || {}
  for (const k of ['province', 'city', 'county', 'street', 'address']) if (!pform.sender[k] && sa[k]) pform.sender[k] = sa[k]
}
async function doPrint() {
  const sd = pform.sender
  if (!sd.name || !sd.mobile || !sd.province || !sd.city || !sd.address) return ElMessage.warning('请填写完整的寄件人信息')
  printing.value = true; printErr.value = ''
  try {
    printStep.value = '正在取号…'
    const { data } = await http.post('/orders/print/waybills', { order_ids: printRows.value.map((r) => r.id), acct_id: pform.acct.acct_id,
      delivery_id: pform.acct.delivery_id, shop_id: pform.acct.shop_id, site_code: pform.acct.site_code, template_id: pform.template_id, sender: sd }, { timeout: 180000 })
    const ok = data.results.filter((r: any) => r.ok && r.print_info)
    const bad = data.results.filter((r: any) => !r.ok)
    if (!ok.length) { printErr.value = bad.map((b: any) => `${b.order_id || b.id}：${b.error}`).join('；'); return }
    printStep.value = `正在打印 ${ok.length} 张面单…`
    const res = await printWaybills(ok.map((r: any) => ({ taskID: String(r.id), printInfo: r.print_info })), pform.printer || undefined)
    const printedOk = ok.filter((r: any) => res[String(r.id)] === true)
    const printFail = ok.filter((r: any) => res[String(r.id)] !== true)
    let shipBad: any[] = []
    if (printedOk.length) {
      printStep.value = pform.ship ? '打印完成，正在发货…' : '打印完成…'
      const d = (await http.post('/orders/print/done', { items: printedOk.map((r: any) => ({ id: r.id, re_print: r.re_print })), ship: pform.ship }, { timeout: 180000 })).data
      shipBad = d.results.filter((r: any) => !r.ok)
    }
    const msgs = [...bad.map((b: any) => `${b.order_id}：取号失败 ${b.error}`), ...printFail.map((r: any) => `${r.order_id}：打印失败 ${res[String(r.id)] || ''}`),
      ...shipBad.map((r: any) => `${r.order_id}：${r.error}`)]
    // the printed labels' receiver: preview each → OCR on the server → shown in the order
    let read = 0
    for (const r of printedOk) {
      printStep.value = `正在读取收件地址 ${read + 1}/${printedOk.length}…`
      try {
        const url = await previewWaybill(r.print_info)
        if (!url) { console.warn('打印组件没有返回预览图'); continue }
        const blob = await (await fetch(url)).blob()
        const fd = new FormData(); fd.append('file', blob, `label-${r.id}.png`)
        await http.post(`/orders/${r.id}/label-image`, fd, { timeout: 120000, headers: { 'X-Silent': '1' } })
        read++
      } catch (e) { console.warn('预览识别失败', e) /* read from the saved PDF instead */ }
    }
    if (msgs.length) printErr.value = msgs.join('；')
    if (read < printedOk.length) needPdf.value = printedOk.length  // wait for the saved PDF
    else if (!msgs.length) { printVisible.value = false; ElMessage.success(`已打印 ${printedOk.length} 张面单，已读取收件地址`) }
    load()
  } catch (e: any) {
    printErr.value = e?.response?.data?.detail || e.message || '打单失败'
  } finally { printing.value = false; printStep.value = '' }
}

// ---- 面单 PDF: the receiver on each label → the order ----
const importing = ref(false)
async function importLabels(opt: any) {
  importing.value = true
  try {
    const fd = new FormData(); fd.append('file', opt.file)
    const { data } = await http.post('/orders/labels/import', fd, { timeout: 600000 })
    const bad = data.results.filter((r: any) => !r.ok)
    if (!bad.length) ElMessage.success(`已读取 ${data.ok} 张面单的收件地址`)
    else ElMessageBox.alert([`成功 ${data.ok} 张，失败 ${data.failed} 张：`, ...bad.map((b: any) => `第 ${b.page} 页：${b.error}${b.address ? '（' + b.address + '）' : ''}`)].join('<br>'),
      '导入面单 PDF', { dangerouslyUseHTMLString: true })
    load()
  } finally { importing.value = false }
}
async function copyText(text: string, ok: string) {
  try { await navigator.clipboard.writeText(text); ElMessage.success(ok) } catch { ElMessage.warning('复制失败') }
}
async function importAfterPrint(opt: any) {
  await importLabels(opt)
  printVisible.value = false
}
async function copyLabel(row: any) {
  const a = row.label_address
  try { await navigator.clipboard.writeText(`${a.name} ${a.phone} ${a.address}`); ElMessage.success('已复制面单地址') } catch { ElMessage.warning('复制失败') }
}
</script>

<style scoped>
.stores { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; margin-bottom: 8px; }
.scard { border: 1px solid #eef0f4; border-radius: 8px; padding: 12px; cursor: pointer; background: #fafbfc; }
.scard.active { border-color: #409eff; background: #ecf5ff; }
.b { font-weight: 600; }
.small { font-size: 12px; }
.warn { color: #e6a23c; }
.danger { color: #f56c6c; }
.ok { color: #67c23a; }
.line { display: flex; gap: 10px; padding: 4px 0; }
.line + .line { border-top: 1px dashed #eef0f4; }
.clickable { cursor: pointer; }
.pid { font-family: monospace; color: #606266; }
.summary { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; padding: 8px 12px; margin-bottom: 10px; background: #f6f8fa; border-radius: 6px; }
.nego-title { font-size: 18px; font-weight: 600; margin-bottom: 4px; }
.nego-foot { display: flex; justify-content: space-between; align-items: center; }
.refund-box { margin-top: 14px; padding: 10px 12px; border: 1px dashed #f3d19e; border-radius: 6px; background: #fdf6ec; }
.nego-line { padding: 4px 0; border-bottom: 1px dashed #eef0f4; }
.chat { max-height: 260px; overflow-y: auto; margin-bottom: 8px; padding: 6px; background: #f6f8fa; border-radius: 6px; }
.bubble { max-width: 80%; margin: 4px 0; padding: 6px 10px; border-radius: 8px; background: #fff; }
.bubble.out { margin-left: auto; background: #e1f3d8; }
.po-box { margin-top: 6px; padding: 4px 6px; border: 1px solid #ffd591; background: #fff7e6; border-radius: 4px; }
.po-label { font-size: 12px; color: #d46b08; }
.po-no { font-size: 14px; font-weight: 700; color: #d46b08; word-break: break-all; }
.po-link { font-size: 12px; margin-left: 8px; color: #ff6a00; }
.po-none { margin-left: 6px; cursor: pointer; }
.pdf-drop { margin-top: 10px; padding: 10px; background: #f0f9eb; border-radius: 6px; }
.label-addr { margin-top: 4px; padding: 4px 6px; background: #f0f9eb; border-radius: 4px; }
.sender { display: flex; flex-wrap: wrap; gap: 6px; width: 100%; }
.clickable:hover { color: var(--el-color-primary); }
.thumb { width: 52px; height: 52px; border-radius: 4px; flex-shrink: 0; }
.info { min-width: 0; flex: 1; }
.ml { margin-left: 6px; }
.pline { display: flex; align-items: center; gap: 8px; justify-content: space-between; margin-bottom: 6px; }
.pline .ellipsis { flex: 1; min-width: 0; }
.raw { max-height: 60vh; overflow: auto; background: #f6f8fa; padding: 10px; border-radius: 6px; font-size: 12px; }
</style>
