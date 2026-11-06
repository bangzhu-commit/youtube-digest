const test = require('node:test');
const assert = require('node:assert/strict');
const speakers = require('../podcast-speakers.js');
const episodeId = 'a'.repeat(24), sourceHash = 'b'.repeat(64);
function fixture() {
  const transcript = { entries: [{ id: 'p-0', text: '你🙂好 后半段', start: 0, speaker: '' }] };
  const data = { version: speakers.VERSION, episodeId, sourceHash, offsetUnit: 'unicode-code-point', wordAlignment: false, duration: 15, speakers: [{ id: 'S1', label: '说话人 1' }, { id: 'S2', label: '说话人 2' }], segments: [
    { id: 's-0', sourceEntryId: 'p-0', sourceStart: 0, sourceEnd: 3, start: 0, end: 4, text: '你🙂好', speakerIds: ['S1'], status: 'single' },
    { id: 's-1', sourceEntryId: 'p-0', sourceStart: 3, sourceEnd: 7, start: 4, end: 10, text: ' 后半段', speakerIds: ['S2'], status: 'single' },
  ] };
  return { data, transcript };
}
const check = (data, transcript) => speakers.validate(data, transcript, sourceHash, episodeId);
test('speaker overlay uses Unicode code points and preserves every original character', () => {
  const { data, transcript } = fixture(), before = JSON.stringify(transcript);
  assert.equal(check(data, transcript), data);
  assert.equal(data.segments.map(item => item.text).join(''), transcript.entries[0].text);
  assert.equal(JSON.stringify(transcript), before);
});
test('speaker overlay rejects mismatched source, episode and UTF-16 offsets', () => {
  for (const mutate of [data => data.sourceHash = 'c'.repeat(64), data => data.episodeId = 'c'.repeat(24), data => data.segments[0].sourceEnd = 4]) {
    const { data, transcript } = fixture(); mutate(data); assert.throws(() => check(data, transcript), /无法逐字对应/);
  }
});
test('speaker overlay rejects missing text, duplicate ranges and invented words', () => {
  for (const mutate of [data => data.segments.pop(), data => data.segments[1].sourceStart = 0, data => data.segments[1].text = '猜出来的话']) {
    const { data, transcript } = fixture(); mutate(data); assert.throws(() => check(data, transcript));
  }
});
test('speaker labels cannot contain inferred identities or refer to missing speakers', () => {
  for (const mutate of [data => data.speakers[0].label = '主持人张三', data => data.segments[0].speakerIds = ['S3'], data => data.segments[0].speakerIds = []]) {
    const { data, transcript } = fixture(); mutate(data); assert.throws(() => check(data, transcript));
  }
});
test('mixed, overlapping and uncertain segments retain distinct uncertainty labels', () => {
  for (const [status, expected] of [['mixed', /片段内换人/], ['overlap', /声音重叠，文字归属不确定/], ['uncertain', /归属不确定/]]) {
    const { data, transcript } = fixture(); Object.assign(data.segments[0], { status, speakerIds: ['S1', 'S2'] });
    check(data, transcript); assert.match(speakers.label(data.segments[0], data), expected);
  }
  const { data, transcript } = fixture(); Object.assign(data.segments[0], { status: 'uncertain', speakerIds: [] });
  check(data, transcript); assert.equal(speakers.label(data.segments[0], data), '说话人不确定');
});
test('speaker ranges must be ordered, finite and within the recorded audio', () => {
  for (const mutate of [data => data.segments[1].start = -1, data => data.segments[0].end = Infinity, data => data.segments[1].end = 1000, data => data.segments.reverse()]) {
    const { data, transcript } = fixture(); mutate(data); assert.throws(() => check(data, transcript));
  }
});
test('an all-uncertain overlay can preserve source text without stable speaker numbers', () => {
  const { data, transcript } = fixture();
  data.speakers = [];
  for (const segment of data.segments) Object.assign(segment, { status: 'uncertain', speakerIds: [] });
  assert.equal(check(data, transcript), data);
  assert.equal(data.segments.map(item => item.text).join(''), transcript.entries[0].text);
  assert.equal(speakers.label(data.segments[0], data), '说话人不确定');
});
test('empty stable-speaker lists reject confident statuses, invented IDs and altered source text', () => {
  for (const mutate of [
    ...['single', 'mixed', 'overlap'].map(status => data => data.segments[0].status = status),
    data => data.segments[0].speakerIds = ['S1'],
    data => data.segments[1].text = '猜出来的话',
  ]) {
    const { data, transcript } = fixture();
    data.speakers = [];
    for (const segment of data.segments) Object.assign(segment, { status: 'uncertain', speakerIds: [] });
    mutate(data); assert.throws(() => check(data, transcript));
  }
});
