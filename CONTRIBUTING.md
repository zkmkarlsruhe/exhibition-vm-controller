# Contributing to Exhibition VM Controller

Thank you for your interest in contributing to the Exhibition VM Controller project! This project aims to provide robust, reliable infrastructure for conserving and exhibiting digital artworks, and we welcome contributions that help advance this goal.

## How to Contribute

### Reporting Issues

If you encounter bugs, problems, or have questions:

1. Check the [existing issues](https://github.com/zkmkarlsruhe/exhibition-vm-controller/issues) to see if your problem has already been reported
2. If not, create a new issue with:
   - A clear, descriptive title
   - Detailed description of the problem
   - Steps to reproduce (if applicable)
   - Your environment (OS, Python version, QEMU/KVM version, etc.)
   - Relevant log files or error messages

### Suggesting Enhancements

We welcome suggestions for new features or improvements:

1. Check existing issues and discussions to avoid duplicates
2. Create a new issue with the "enhancement" label
3. Clearly describe:
   - The use case or problem you're trying to solve
   - Your proposed solution
   - Any alternative approaches you've considered
   - How this would benefit the broader community

### Contributing Code

#### Areas We're Particularly Interested In

- **Additional Guest OS Examples**: Scripts for Mac OS 9, older Windows versions, or Linux guests
- **Improved Error Detection**: New strategies for detecting failures in guest systems
- **Documentation**: Tutorials, case studies, troubleshooting guides
- **Alternative Virtualization Backends**: Support for other hypervisors beyond QEMU/KVM
- **Testing Infrastructure**: Automated tests, CI/CD pipelines
- **Monitoring Improvements**: Better logging, metrics, dashboards
- **Network Service Mocking**: Tools for creating local replicas of historical web services

#### Development Workflow

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/exhibition-vm-controller
   cd exhibition-vm-controller
   ```
3. **Create a feature branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Make your changes**:
   - Follow existing code style and conventions
   - Add comments where logic is non-obvious
   - Update documentation as needed
5. **Test your changes** thoroughly in a VM environment
6. **Commit your changes**:
   ```bash
   git add .
   git commit -m "Add: brief description of your changes"
   ```
7. **Push to your fork**:
   ```bash
   git push origin feature/your-feature-name
   ```
8. **Create a Pull Request** on GitHub with:
   - Clear description of what you've changed and why
   - Reference to any related issues
   - Screenshots or examples if applicable

#### Code Style Guidelines

**Python Code**:
- Follow [PEP 8](https://pep8.org/) style guide
- Use type hints where appropriate
- Write docstrings for public functions and classes
- Keep functions focused and reasonably sized

**Guest Scripts (AutoIt, Shell, etc.)**:
- Add clear comments explaining what each section does
- Use descriptive variable names
- Document any magic numbers or timeouts
- Include examples of expected behavior

**Documentation**:
- Use clear, concise language
- Include practical examples
- Consider the audience (museum technicians, conservators, developers)
- Update the README.md if your changes affect setup or usage

### Contributing Documentation

Documentation improvements are highly valued:

- Fix typos or unclear explanations
- Add missing information
- Create tutorials or guides
- Translate documentation to other languages
- Document edge cases or known issues

### Testing

Before submitting a pull request:

1. **Test in a real VM environment** if possible
2. **Verify existing functionality** still works
3. **Test edge cases** and error conditions
4. **Document your testing process** in the PR description

Currently, we don't have automated tests, but we'd welcome contributions in this area!

## Code of Conduct

### Our Standards

- Be respectful and inclusive
- Welcome newcomers and help them get started
- Focus on constructive feedback
- Acknowledge different perspectives and experiences
- Prioritize the needs of the community and the artworks we're trying to preserve

### Unacceptable Behavior

- Harassment, discrimination, or personal attacks
- Trolling or deliberately derailing discussions
- Publishing others' private information
- Any conduct that would be inappropriate in a professional setting

## Recognition

Contributors will be:

- Listed in the [AUTHORS](AUTHORS) file
- Credited in release notes
- Acknowledged in any academic publications that result from this work

## Questions?

If you have questions about contributing:

- Open an issue with the "question" label
- Email the maintainer: mschuetze@zkm.de

## License

By contributing, you agree that your contributions will be licensed under the MIT License, the same license as the project.

## Special Considerations for Museum and Conservation Contexts

This project is used in real exhibition environments where stability and reliability are critical. When contributing, please consider:

- **Backward compatibility**: Changes should not break existing installations
- **Fail-safe behavior**: Systems should fail gracefully rather than catastrophically
- **Clear documentation**: Museum technicians may not be software developers
- **Tested in practice**: Theoretical improvements should be validated in real scenarios
- **Minimal dependencies**: Avoid adding unnecessary dependencies that could become obsolete

Thank you for helping preserve digital art history!

---

*This contributing guide is inspired by best practices from the open source community and adapted for the specific needs of digital art conservation.*
