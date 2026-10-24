export default class RealLaunchReporter {
  matched = null;
  noTests = false;

  onBegin(_config, suite) {
    this.matched = suite.allTests().length;
    console.log(`\nDIAG-REAL-MATCHED ${this.matched}`);
  }

  onError(error) {
    this.noTests ||= /No tests found\b/.test(error.message);
  }

  onTestEnd(_test, result) {
    console.log(`\nDIAG-REAL-TEST ${result.status}`);
  }

  onEnd() {
    if (this.matched === null && this.noTests) console.log('\nDIAG-REAL-MATCHED 0');
  }
}
