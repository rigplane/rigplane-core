const propertyName = node => node?.type === 'Identifier' ? node.name
  : node?.type === 'Literal' ? node.value : undefined;

export default {
  rules: {
    'condition-waits': {
      meta: {
        type: 'problem', schema: [],
        messages: {
          sleep: 'Use a pipeline drain or an observed condition; bare waitForTimeout is forbidden in journeys.',
          action: 'Use the shared panorama action helper or an explicit action timeout.',
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
