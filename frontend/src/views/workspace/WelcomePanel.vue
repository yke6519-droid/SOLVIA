<script setup>
defineEmits(['use-shortcut', 'import-data'])
</script>

<template>
  <section class="welcome-panel">
    <div class="sun-orbit orbit-one"></div>
    <div class="sun-orbit orbit-two"></div>

    <div class="welcome-copy">
      <p class="eyebrow">欢迎进入光伏运营工作台</p>
      <h1 class="welcome-title">
        <span class="welcome-title-line welcome-title-left">把一个光伏问题，</span>
        <span class="welcome-title-line welcome-title-right">
          <span class="inline-solar">交给阳光</span>和数据。
        </span>
      </h1>
      <p class="welcome-description">
        查询、预测、导入、复盘。用一句自然语言开始一项真实的运营任务。
      </p>
    </div>

    <div class="shortcut-grid">
      <a-button
        class="shortcut-card"
        type="text"
        @click="$emit('use-shortcut', '查询英杰站今天的实际发电量')"
      >
        <span class="shortcut-kicker">实际数据</span>
        <strong>查询发电量</strong>
        <span>按站点和日期获取真实记录</span>
        <span class="shortcut-arrow">↗</span>
      </a-button>
      <a-button
        class="shortcut-card accent"
        type="text"
        @click="$emit('use-shortcut', '预测英杰站明天的发电量')"
      >
        <span class="shortcut-kicker">预测任务</span>
        <strong>预测未来发电</strong>
        <span>确认条件后运行预测模型</span>
        <span class="shortcut-arrow">↗</span>
      </a-button>
      <a-button class="shortcut-card" type="text" @click="$emit('import-data')">
        <span class="shortcut-kicker">数据资产</span>
        <strong>导入数据文件</strong>
        <span>预览、清洗并确认入库</span>
        <span class="shortcut-arrow">↗</span>
      </a-button>
    </div>
  </section>
</template>
