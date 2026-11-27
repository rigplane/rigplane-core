const propertyName = node => node?.type === 'Identifier' ? node.name
  : node?.type === 'Literal' ? node.value : undefined;

export default {
  rules: {
    'condition-waits': {
      meta: {
        type: 'problem', schema: [],
        messages: {
          sleep: 'Bare waitForTimeout is forbidden in journey specs: wait on the render pipeline with awaitRenderPipeline(page) or observePanorama(page, ms) from ./panorama-ready, or on a condition (expectPanoramaFrequency, expectPassbandProbeReady from ./panorama-passband-probe).',
          action: 'Click/reload in a journey spec must go through clickPanorama(page, locator) or reloadPanorama(page) from ./panorama-ready (they drain the render pipeline), or pass an explicit { timeout } tied to the action.',
        },
      },
      create(context) {
        return {
          CallExpression(node) {
            if (node.callee.type !== 'MemberExpression') return;
            const member = node.callee;
            const name = propertyName(member.property);
            if (name === 'waitForTimeout') {
              context.report({ node, messageId: 'sleep' });
              return;
            }
            if (name !== 'click' && name !== 'reload') return;
            // Mouse clicks take coordinates and have no actionability timeout.
            if (name === 'click' && member.object.type === 'MemberExpression'
              && propertyName(member.object.property) === 'mouse') return;
            const options = node.arguments.at(-1);
            const explicit = options?.type === 'ObjectExpression' && options.properties.some(
              property => property.type === 'Property' && propertyName(property.key) === 'timeout');
            if (!explicit) context.report({ node, messageId: 'action' });
          },
        };
      },
    },
  },
};
